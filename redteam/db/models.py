from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from redteam.runner.models import Severity


@dataclass
class StoredRun:
    """DB representation of a completed run (maps to the `runs` table)."""
    run_id: str
    timestamp: datetime
    target_model: str
    target_provider: str
    probe_categories: list[str]
    garak_version: str
    duration_seconds: float
    exit_code: int
    error_message: str


@dataclass
class StoredProbeResult:
    """DB representation of one probe outcome (maps to `probe_results`)."""
    id: int | None          # None before insert
    run_id: str
    probe_category: str
    probe_name: str
    status: str
    severity: str
    description: str
    attempts: int
    failures: int
    raw_output: str

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    @property
    def severity_enum(self) -> Severity:
        try:
            return Severity(self.severity)
        except ValueError:
            return Severity.UNKNOWN


@dataclass
class Regression:
    """
    A probe that passed in run_a but failed in run_b.
    This is the core signal -- new vulnerability introduced between two runs.
    """
    probe_category: str
    probe_name: str
    severity: Severity
    description: str
    run_a_id: str       # the baseline (older) run
    run_b_id: str       # the current run
    # Raw outputs from both runs for side-by-side debugging
    run_a_raw: str
    run_b_raw: str


@dataclass
class Fix:
    """
    A probe that failed in run_a but passed in run_b.
    Tracked alongside regressions so reports show net movement.
    """
    probe_category: str
    probe_name: str
    severity: Severity
    run_a_id: str
    run_b_id: str
