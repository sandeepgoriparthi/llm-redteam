from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from redteam.db.repository import RunRepository
from redteam.runner.models import ProbeResult, ProbeStatus, RunResult, Severity


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
def repo(tmp_path: Path) -> RunRepository:
    return RunRepository(tmp_path / "test.db")


def _make_run(
    run_id: str,
    probe_results: list[ProbeResult],
    model: str = "gpt-4o",
) -> RunResult:
    return RunResult(
        run_id=run_id,
        timestamp=datetime.now(tz=timezone.utc),
        target_model=model,
        target_provider="openai",
        probe_categories=["dan", "gcg"],
        probe_results=probe_results,
        garak_version="0.9.0",
        duration_seconds=42.0,
        exit_code=0,
    )


def _make_probe(
    category: str,
    name: str,
    status: ProbeStatus,
    severity: Severity = Severity.HIGH,
) -> ProbeResult:
    return ProbeResult(
        probe_category=category,
        probe_name=name,
        status=status,
        severity=severity,
        description=f"{name} probe",
        raw_output="{}",
        attempts=10,
        failures=0 if status == ProbeStatus.PASSED else 3,
    )


# ------------------------------------------------------------------
# Save + retrieve
# ------------------------------------------------------------------

def test_save_and_get_run(repo: RunRepository):
    run = _make_run("run-001", [
        _make_probe("dan", "DAN_11_0", ProbeStatus.FAILED),
        _make_probe("gcg", "GCGCached", ProbeStatus.PASSED),
    ])
    repo.save_run(run)

    stored = repo.get_run("run-001")
    assert stored is not None
    assert stored.run_id == "run-001"
    assert stored.target_model == "gpt-4o"
    assert stored.duration_seconds == pytest.approx(42.0)


def test_get_probe_results(repo: RunRepository):
    run = _make_run("run-002", [
        _make_probe("dan", "DAN_11_0", ProbeStatus.FAILED),
        _make_probe("gcg", "GCGCached", ProbeStatus.PASSED),
    ])
    repo.save_run(run)

    probes = repo.get_probe_results("run-002")
    assert len(probes) == 2
    statuses = {p.probe_name: p.status for p in probes}
    assert statuses["DAN_11_0"] == "failed"
    assert statuses["GCGCached"] == "passed"


def test_list_runs_ordered_by_timestamp(repo: RunRepository):
    repo.save_run(_make_run("run-a", []))
    repo.save_run(_make_run("run-b", []))
    repo.save_run(_make_run("run-c", []))

    runs = repo.list_runs()
    assert [r.run_id for r in runs] == ["run-c", "run-b", "run-a"]


def test_get_latest_run_id(repo: RunRepository):
    repo.save_run(_make_run("run-x", []))
    repo.save_run(_make_run("run-y", []))
    assert repo.get_latest_run_id() == "run-y"


def test_get_nonexistent_run_returns_none(repo: RunRepository):
    assert repo.get_run("does-not-exist") is None


# ------------------------------------------------------------------
# Diff / regression detection
# ------------------------------------------------------------------

def test_diff_detects_regression(repo: RunRepository):
    """Probe passes in run_a, fails in run_b -> regression."""
    run_a = _make_run("run-a", [
        _make_probe("dan", "DAN_11_0", ProbeStatus.PASSED),
    ])
    run_b = _make_run("run-b", [
        _make_probe("dan", "DAN_11_0", ProbeStatus.FAILED),
    ])
    repo.save_run(run_a)
    repo.save_run(run_b)

    regressions, fixes = repo.diff("run-a", "run-b")
    assert len(regressions) == 1
    assert len(fixes) == 0
    assert regressions[0].probe_name == "DAN_11_0"
    assert regressions[0].severity == Severity.HIGH


def test_diff_detects_fix(repo: RunRepository):
    """Probe fails in run_a, passes in run_b -> fix."""
    run_a = _make_run("run-a", [
        _make_probe("dan", "DAN_11_0", ProbeStatus.FAILED),
    ])
    run_b = _make_run("run-b", [
        _make_probe("dan", "DAN_11_0", ProbeStatus.PASSED),
    ])
    repo.save_run(run_a)
    repo.save_run(run_b)

    regressions, fixes = repo.diff("run-a", "run-b")
    assert len(regressions) == 0
    assert len(fixes) == 1
    assert fixes[0].probe_name == "DAN_11_0"


def test_diff_ignores_errored_probes(repo: RunRepository):
    """Error status in either run should not produce regressions."""
    run_a = _make_run("run-a", [
        _make_probe("dan", "DAN_11_0", ProbeStatus.PASSED),
    ])
    run_b = _make_run("run-b", [
        _make_probe("dan", "DAN_11_0", ProbeStatus.ERROR),
    ])
    repo.save_run(run_a)
    repo.save_run(run_b)

    regressions, fixes = repo.diff("run-a", "run-b")
    assert len(regressions) == 0
    assert len(fixes) == 0


def test_diff_no_change(repo: RunRepository):
    """Same results in both runs -> no regressions, no fixes."""
    probes = [
        _make_probe("dan", "DAN_11_0", ProbeStatus.FAILED),
        _make_probe("gcg", "GCGCached", ProbeStatus.PASSED),
    ]
    repo.save_run(_make_run("run-a", probes))
    repo.save_run(_make_run("run-b", probes))

    regressions, fixes = repo.diff("run-a", "run-b")
    assert regressions == []
    assert fixes == []


def test_diff_mixed(repo: RunRepository):
    """One regression + one fix in the same diff."""
    run_a = _make_run("run-a", [
        _make_probe("dan", "DAN_11_0", ProbeStatus.PASSED),   # will regress
        _make_probe("gcg", "GCGCached", ProbeStatus.FAILED),  # will be fixed
    ])
    run_b = _make_run("run-b", [
        _make_probe("dan", "DAN_11_0", ProbeStatus.FAILED),
        _make_probe("gcg", "GCGCached", ProbeStatus.PASSED),
    ])
    repo.save_run(run_a)
    repo.save_run(run_b)

    regressions, fixes = repo.diff("run-a", "run-b")
    assert len(regressions) == 1
    assert len(fixes) == 1
