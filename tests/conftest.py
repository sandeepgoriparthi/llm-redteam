from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from redteam.db.repository import RunRepository
from redteam.runner.models import ProbeResult, ProbeStatus, RunResult, Severity


@pytest.fixture
def tmp_db(tmp_path: Path) -> RunRepository:
    """In-memory-backed SQLite repo for tests that need DB state."""
    return RunRepository(tmp_path / "test.db")


@pytest.fixture
def sample_run_passed() -> RunResult:
    return RunResult(
        run_id="run-pass-001",
        timestamp=datetime.now(tz=timezone.utc),
        target_model="gpt-4o",
        target_provider="openai",
        probe_categories=["dan"],
        probe_results=[
            ProbeResult("dan", "DAN_11_0", ProbeStatus.PASSED, Severity.HIGH,
                        "DAN probe", "{}", 10, 0),
        ],
        garak_version="0.9.0",
        duration_seconds=30.0,
    )


@pytest.fixture
def sample_run_failed() -> RunResult:
    return RunResult(
        run_id="run-fail-001",
        timestamp=datetime.now(tz=timezone.utc),
        target_model="gpt-4o",
        target_provider="openai",
        probe_categories=["dan"],
        probe_results=[
            ProbeResult("dan", "DAN_11_0", ProbeStatus.FAILED, Severity.HIGH,
                        "DAN probe", "{}", 10, 6),
        ],
        garak_version="0.9.0",
        duration_seconds=31.0,
    )
