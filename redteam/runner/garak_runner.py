from __future__ import annotations

import json
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from redteam.config import Settings
from redteam.runner.models import (
    ProbeResult,
    ProbeStatus,
    RunResult,
    Severity,
)

# Garak severity strings -> our enum.
# Tested against 0.14.x. If garak changes output format, bump this and
# re-check _build_garak_command and _parse_probe_result.
TESTED_GARAK_VERSION = "0.14"

_SEVERITY_MAP: dict[str, Severity] = {
    "CRITICAL": Severity.CRITICAL,
    "HIGH": Severity.HIGH,
    "MEDIUM": Severity.MEDIUM,
    "LOW": Severity.LOW,
    "INFO": Severity.INFO,
}

_STATUS_MAP: dict[str, ProbeStatus] = {
    "passed": ProbeStatus.PASSED,
    "failed": ProbeStatus.FAILED,
    "error": ProbeStatus.ERROR,
    "skipped": ProbeStatus.SKIPPED,
}


class GarakNotFoundError(RuntimeError):
    pass


class GarakVersionMismatchWarning(UserWarning):
    pass


class GarakRunError(RuntimeError):
    """Raised when garak exits non-zero and retries are exhausted."""
    pass


def _get_garak_version() -> str:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "garak", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # garak --version prints something like "garak 0.9.0.post1"
        return result.stdout.strip().split()[-1]
    except Exception:
        return "unknown"


def _check_garak_available() -> str:
    """Verify garak is importable and return its version. Raises if not found."""
    version = _get_garak_version()
    if version == "unknown":
        raise GarakNotFoundError(
            "garak not found. Install with: pip install garak"
        )
    if not version.startswith(TESTED_GARAK_VERSION):
        import warnings
        warnings.warn(
            f"Installed garak {version} differs from tested version "
            f"{TESTED_GARAK_VERSION}.x. Output parsing may break. "
            f"Check TESTED_GARAK_VERSION in garak_runner.py.",
            GarakVersionMismatchWarning,
            stacklevel=2,
        )
    return version


def _build_garak_command(
    settings: Settings,
    probe_categories: list[str],
    report_path: Path,
    system_prompt: str | None = None,
) -> list[str]:
    """
    Construct the garak subprocess command for garak 0.14.x.

    Breaking changes from 0.9.x:
    - --probe_spec removed; use --probes with short names (e.g. "dan" not "garak.probes.dan")
    - --format flag removed; garak always writes .report.jsonl alongside .hitlog.jsonl
    - --model_type aliased to --target_type but old flag still accepted

    Tradeoff: --probes with category names (e.g. "dan") runs all probes in that
    category. This is less granular than individual probe names but more stable
    across minor versions -- category names change far less often than probe class names.
    """
    probe_spec = ",".join(probe_categories)   # "dan,gcg" -- no prefix needed in 0.14.x

    cmd = [
        sys.executable, "-m", "garak",
        "--model_type", _provider_to_garak_type(settings.target_provider),
        "--model_name", settings.target_model,
        "--probes", probe_spec,
        "--report_prefix", str(report_path),
    ]

    if settings.target_provider == "ollama":
        cmd += ["--generator_option", f"uri={settings.ollama_host}"]
    elif settings.target_provider == "custom":
        cmd += ["--generator_option", f"uri={settings.target_endpoint}"]

    if system_prompt:
        cmd += ["--system_prompt", system_prompt]

    return cmd


def _provider_to_garak_type(provider: str) -> str:
    mapping = {
        "openai": "openai",
        "ollama": "ollama",
        "custom": "rest",
    }
    return mapping.get(provider, "rest")


def _parse_jsonl_report(report_file: Path) -> Iterator[dict]:
    """
    Yield parsed JSON objects from garak's .jsonl report.

    Garak writes one JSON object per line. Lines that are not valid JSON
    (e.g. progress markers) are skipped with a warning.
    """
    if not report_file.exists():
        return

    with report_file.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                # garak occasionally writes non-JSON progress lines
                continue


def _parse_probe_result(record: dict) -> ProbeResult | None:
    """
    Convert one garak report record into a ProbeResult.

    Returns None for record types we don't care about (e.g. run metadata).

    Garak 0.9.x report schema (relevant fields):
      - type: "probe_result" | "run_start" | "run_end" | ...
      - probe: "garak.probes.dan.DAN_11_0"
      - status: "passed" | "failed" | "error" | "skipped"
      - severity: "HIGH" | ...
      - description: str
      - attempts: int
      - failures: int
    """
    if record.get("type") != "probe_result":
        return None

    probe_full = record.get("probe", "unknown.unknown")
    # 0.14.x format: "dan.Ablation_Dan_11_0" -> category="dan", name="Ablation_Dan_11_0"
    # 0.9.x format:  "garak.probes.dan.DAN_11_0" -> handled by fallback
    parts = probe_full.split(".")
    if len(parts) == 2:
        # 0.14.x short format
        category, name = parts[0], parts[1]
    elif len(parts) >= 4:
        # 0.9.x long format
        category = parts[2]
        name = parts[3]
    else:
        category, name = "unknown", probe_full

    raw_status = record.get("status", "error").lower()
    raw_severity = record.get("severity", "UNKNOWN").upper()

    return ProbeResult(
        probe_category=category,
        probe_name=name,
        status=_STATUS_MAP.get(raw_status, ProbeStatus.ERROR),
        severity=_SEVERITY_MAP.get(raw_severity, Severity.UNKNOWN),
        description=record.get("description", ""),
        raw_output=json.dumps(record),
        attempts=record.get("attempts", 0),
        failures=record.get("failures", 0),
    )


class GarakRunner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.garak_version = _check_garak_available()

    @retry(
        retry=retry_if_exception_type(GarakRunError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        reraise=True,
    )
    def run(
        self,
        probe_categories: list[str] | None = None,
        system_prompt: str | None = None,
    ) -> RunResult:
        """
        Execute a full garak scan and return structured results.

        probe_categories defaults to settings.garak_probe_categories.
        system_prompt overrides the target's system prompt (used during retest).
        """
        categories = probe_categories or self.settings.garak_probe_categories
        run_id = str(uuid.uuid4())
        timestamp = datetime.now(tz=timezone.utc)

        # garak writes its report to <prefix>.report.jsonl
        report_prefix = Path("data") / f"garak_run_{run_id}"
        report_file = report_prefix.with_suffix(".report.jsonl")

        cmd = _build_garak_command(
            self.settings,
            categories,
            report_prefix,
            system_prompt=system_prompt,
        )

        start = time.monotonic()
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self.settings.garak_timeout,
            env=self._build_env(),
        )
        duration = time.monotonic() - start

        if proc.returncode != 0:
            raise GarakRunError(
                f"garak exited {proc.returncode}.\n"
                f"stdout: {proc.stdout[-2000:]}\n"
                f"stderr: {proc.stderr[-2000:]}"
            )

        probe_results: list[ProbeResult] = []
        for record in _parse_jsonl_report(report_file):
            result = _parse_probe_result(record)
            if result is not None:
                probe_results.append(result)

        # Clean up the temp report file -- we store results in SQLite
        if report_file.exists():
            report_file.unlink()

        return RunResult(
            run_id=run_id,
            timestamp=timestamp,
            target_model=self.settings.target_model,
            target_provider=self.settings.target_provider,
            probe_categories=categories,
            probe_results=probe_results,
            garak_version=self.garak_version,
            duration_seconds=duration,
            exit_code=proc.returncode,
        )

    def _build_env(self) -> dict[str, str]:
        """
        Build the environment dict passed to the garak subprocess.
        Injects API keys without leaking them into the command args.
        """
        import os
        env = os.environ.copy()
        if self.settings.openai_api_key:
            env["OPENAI_API_KEY"] = self.settings.openai_api_key
        return env
