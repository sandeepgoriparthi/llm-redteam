from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"
    UNKNOWN = "unknown"


# Garak probe statuses as reported in its .jsonl output
class ProbeStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"      # probe errored out, not a vulnerability result
    SKIPPED = "skipped"


@dataclass
class ProbeResult:
    """Single probe outcome from one Garak run."""
    probe_category: str
    probe_name: str
    status: ProbeStatus
    severity: Severity
    description: str        # human-readable from garak output
    raw_output: str         # full raw json line, kept for debugging
    attempts: int = 0       # how many prompts garak tried
    failures: int = 0       # how many succeeded in breaking the model

    @property
    def passed(self) -> bool:
        return self.status == ProbeStatus.PASSED

    @property
    def failure_rate(self) -> float:
        if self.attempts == 0:
            return 0.0
        return self.failures / self.attempts


@dataclass
class RunResult:
    """Full output of one Garak run."""
    run_id: str             # uuid4, assigned before run starts
    timestamp: datetime
    target_model: str
    target_provider: str
    probe_categories: list[str]
    probe_results: list[ProbeResult] = field(default_factory=list)
    garak_version: str = ""
    duration_seconds: float = 0.0
    exit_code: int = 0
    error_message: str = ""   # populated if garak subprocess failed

    @property
    def total_probes(self) -> int:
        return len(self.probe_results)

    @property
    def failed_probes(self) -> list[ProbeResult]:
        return [r for r in self.probe_results if not r.passed]

    @property
    def passed_probes(self) -> list[ProbeResult]:
        return [r for r in self.probe_results if r.passed]

    @property
    def errored_probes(self) -> list[ProbeResult]:
        return [r for r in self.probe_results if r.status == ProbeStatus.ERROR]

    @property
    def success_rate(self) -> float:
        if self.total_probes == 0:
            return 0.0
        return len(self.passed_probes) / self.total_probes
