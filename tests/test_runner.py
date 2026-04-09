from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from redteam.runner.models import ProbeStatus, RunResult, Severity
from redteam.runner.garak_runner import (
    GarakRunner,
    _parse_probe_result,
    _parse_jsonl_report,
)


# --- Fixtures ---

SAMPLE_PROBE_RECORD = {
    "type": "probe_result",
    "probe": "garak.probes.dan.DAN_11_0",
    "status": "failed",
    "severity": "HIGH",
    "description": "DAN 11.0 jailbreak succeeded",
    "attempts": 10,
    "failures": 3,
}

SAMPLE_META_RECORD = {
    "type": "run_start",
    "timestamp": "2025-01-01T00:00:00Z",
}


# --- Unit tests for parsers ---

def test_parse_probe_result_failure():
    result = _parse_probe_result(SAMPLE_PROBE_RECORD)
    assert result is not None
    assert result.probe_category == "dan"
    assert result.probe_name == "DAN_11_0"
    assert result.status == ProbeStatus.FAILED
    assert result.severity == Severity.HIGH
    assert result.attempts == 10
    assert result.failures == 3
    assert not result.passed
    assert result.failure_rate == pytest.approx(0.3)


def test_parse_probe_result_ignores_non_probe_records():
    result = _parse_probe_result(SAMPLE_META_RECORD)
    assert result is None


def test_parse_probe_result_unknown_severity():
    record = {**SAMPLE_PROBE_RECORD, "severity": "SUPERCRITICAL"}
    result = _parse_probe_result(record)
    assert result is not None
    assert result.severity == Severity.UNKNOWN


def test_parse_jsonl_skips_invalid_lines(tmp_path: Path):
    report = tmp_path / "run.report.jsonl"
    lines = [
        json.dumps(SAMPLE_PROBE_RECORD),
        "not valid json {{{{",
        "",
        json.dumps(SAMPLE_META_RECORD),
    ]
    report.write_text("\n".join(lines))

    records = list(_parse_jsonl_report(report))
    assert len(records) == 2   # invalid line skipped, blank line skipped


def test_parse_jsonl_missing_file(tmp_path: Path):
    records = list(_parse_jsonl_report(tmp_path / "nonexistent.jsonl"))
    assert records == []


# --- Integration-style test for GarakRunner (mocked subprocess) ---

def test_garak_runner_returns_run_result(tmp_path: Path, monkeypatch):
    """
    Mock the subprocess call and a fake report file to verify
    GarakRunner.run() returns a correctly structured RunResult.
    """
    fake_report_content = "\n".join([
        json.dumps(SAMPLE_PROBE_RECORD),
        json.dumps({**SAMPLE_PROBE_RECORD, "probe": "garak.probes.gcg.GCGCached",
                    "status": "passed", "severity": "MEDIUM", "failures": 0}),
    ])

    # Patch subprocess.run to succeed
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = ""
    mock_proc.stderr = ""

    # Patch _get_garak_version to avoid needing garak installed
    monkeypatch.setattr(
        "redteam.runner.garak_runner._get_garak_version",
        lambda: "0.9.0"
    )

    # We need the report file to exist when the runner looks for it
    def fake_subprocess_run(cmd, **kwargs):
        # Write a fake report file based on the run_id in the cmd
        prefix_arg = cmd[cmd.index("--report_prefix") + 1]
        report_file = Path(prefix_arg).with_suffix(".report.jsonl")
        report_file.parent.mkdir(parents=True, exist_ok=True)
        report_file.write_text(fake_report_content)
        return mock_proc

    monkeypatch.setattr("subprocess.run", fake_subprocess_run)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()

    from redteam.config import Settings
    settings = Settings(
        target_provider="openai",
        target_model="gpt-4o",
        openai_api_key="sk-test",
        db_path=tmp_path / "test.db",
        reports_dir=tmp_path / "reports",
    )

    runner = GarakRunner(settings)
    result = runner.run(probe_categories=["dan", "gcg"])

    assert isinstance(result, RunResult)
    assert result.total_probes == 2
    assert len(result.failed_probes) == 1
    assert len(result.passed_probes) == 1
    assert result.target_model == "gpt-4o"
    assert result.exit_code == 0
