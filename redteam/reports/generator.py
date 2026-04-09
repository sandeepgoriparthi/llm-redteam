from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from redteam.agent.state import AgentState, PatchSuggestion, PrioritizedFinding
from redteam.db.models import Fix, Regression
from redteam.runner.models import RunResult

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"


@dataclass
class ReportData:
    """
    Fully assembled report payload.
    Constructed once, rendered to both Markdown and JSON from the same object.
    """
    generated_at: str
    run_id: str
    run_timestamp: str
    target_model: str
    target_provider: str
    probe_categories: list[str]
    garak_version: str
    duration_seconds: float
    total_probes: int
    passed_probes: int
    failed_probes: int
    success_rate: float             # 0.0 - 1.0
    baseline_run_id: str | None

    regressions: list[Regression]   # new failures vs baseline
    fixes: list[Fix]                # resolved failures vs baseline
    top_findings: list[PrioritizedFinding]   # top 10 by priority score
    patches: list[PatchSuggestion]  # LLM-generated patches
    errors: list[str]               # non-fatal errors from the run

    @property
    def has_regressions(self) -> bool:
        return len(self.regressions) > 0

    @property
    def top_patches(self) -> list[PatchSuggestion]:
        """Top 3 patches for the report summary section."""
        confirmed = [p for p in self.patches if p.confirmed]
        unconfirmed = [p for p in self.patches if not p.confirmed]
        ordered = confirmed + unconfirmed
        return ordered[:3]

    @property
    def overall_status(self) -> str:
        if self.errors and self.total_probes == 0:
            return "ERROR"
        if self.has_regressions:
            return "REGRESSION"
        if self.failed_probes == 0:
            return "CLEAN"
        return "FAILURES"

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "overall_status": self.overall_status,
            "run": {
                "run_id": self.run_id,
                "timestamp": self.run_timestamp,
                "target_model": self.target_model,
                "target_provider": self.target_provider,
                "probe_categories": self.probe_categories,
                "garak_version": self.garak_version,
                "duration_seconds": self.duration_seconds,
                "total_probes": self.total_probes,
                "passed_probes": self.passed_probes,
                "failed_probes": self.failed_probes,
                "success_rate_pct": round(self.success_rate * 100, 1),
            },
            "baseline_run_id": self.baseline_run_id,
            "regressions": [
                {
                    "probe_category": r.probe_category,
                    "probe_name": r.probe_name,
                    "severity": r.severity.value,
                    "description": r.description,
                }
                for r in self.regressions
            ],
            "fixes": [
                {
                    "probe_category": f.probe_category,
                    "probe_name": f.probe_name,
                    "severity": f.severity.value,
                }
                for f in self.fixes
            ],
            "top_findings": [
                {
                    "probe_category": f.probe_category,
                    "probe_name": f.probe_name,
                    "severity": f.severity.value,
                    "is_regression": f.is_regression,
                    "failure_rate_pct": round(f.failure_rate * 100, 1),
                    "priority_score": f.priority_score,
                }
                for f in self.top_findings
            ],
            "patches": [
                {
                    "probe_category": p.probe_category,
                    "probe_name": p.probe_name,
                    "severity": p.severity.value,
                    "patch_text": p.patch_text,
                    "rationale": p.rationale,
                    "confirmed": p.confirmed,
                }
                for p in self.patches
            ],
            "errors": self.errors,
        }


def build_report_data(state: AgentState) -> ReportData:
    """Assemble ReportData from agent state. Pure function -- no I/O."""
    run: RunResult | None = state.get("run_result")
    now = datetime.now(tz=timezone.utc).isoformat()

    return ReportData(
        generated_at=now,
        run_id=run.run_id if run else "unknown",
        run_timestamp=run.timestamp.isoformat() if run else now,
        target_model=state.get("target_model", ""),
        target_provider=state.get("target_provider", ""),
        probe_categories=state.get("probe_categories", []),
        garak_version=run.garak_version if run else "",
        duration_seconds=run.duration_seconds if run else 0.0,
        total_probes=run.total_probes if run else 0,
        passed_probes=len(run.passed_probes) if run else 0,
        failed_probes=len(run.failed_probes) if run else 0,
        success_rate=run.success_rate if run else 0.0,
        baseline_run_id=state.get("baseline_run_id"),
        regressions=state.get("regressions", []),
        fixes=state.get("fixes", []),
        top_findings=state.get("prioritized_findings", [])[:10],
        patches=state.get("patches", []),
        errors=state.get("errors", []),
    )


class ReportGenerator:
    def __init__(self, reports_dir: Path) -> None:
        self.reports_dir = reports_dir
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self._jinja_env = Environment(
            loader=FileSystemLoader(str(_TEMPLATES_DIR)),
            undefined=StrictUndefined,
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def write(self, data: ReportData) -> tuple[Path, Path]:
        """
        Write Markdown and JSON reports. Returns (md_path, json_path).
        The timestamp in the filename is UTC to avoid ambiguity across timezones.
        """
        stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        md_path = self.reports_dir / f"report_{stamp}.md"
        json_path = self.reports_dir / f"report_{stamp}.json"

        self._write_json(data, json_path)
        self._write_markdown(data, md_path)

        return md_path, json_path

    def _write_json(self, data: ReportData, path: Path) -> None:
        path.write_text(
            json.dumps(data.to_dict(), indent=2, default=str),
            encoding="utf-8",
        )
        logger.info("JSON report: %s", path)

    def _write_markdown(self, data: ReportData, path: Path) -> None:
        template = self._jinja_env.get_template("report.md.j2")
        content = template.render(data=data)
        path.write_text(content, encoding="utf-8")
        logger.info("Markdown report: %s", path)
