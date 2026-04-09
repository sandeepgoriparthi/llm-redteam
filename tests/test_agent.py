from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from redteam.agent.state import AgentState, PrioritizedFinding, PatchSuggestion
from redteam.agent.nodes.parse_results import parse_results
from redteam.agent.nodes.prioritize import prioritize
from redteam.db.models import Fix, Regression
from redteam.runner.models import ProbeResult, ProbeStatus, RunResult, Severity


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_run(probes: list[ProbeResult]) -> RunResult:
    return RunResult(
        run_id="test-run-001",
        timestamp=datetime.now(tz=timezone.utc),
        target_model="gpt-4o",
        target_provider="openai",
        probe_categories=["dan"],
        probe_results=probes,
        garak_version="0.9.0",
    )


def _base_state(**overrides) -> AgentState:
    state: AgentState = {
        "target_model": "gpt-4o",
        "target_provider": "openai",
        "probe_categories": ["dan"],
        "system_prompt": None,
        "run_result": None,
        "baseline_run_id": None,
        "regressions": [],
        "fixes": [],
        "prioritized_findings": [],
        "patches": [],
        "retest_results": [],
        "report_path": "",
        "report_json": {},
        "errors": [],
    }
    state.update(overrides)
    return state


def _probe(name: str, status: ProbeStatus, severity: Severity = Severity.HIGH,
           attempts: int = 10, failures: int = 5) -> ProbeResult:
    return ProbeResult(
        probe_category="dan",
        probe_name=name,
        status=status,
        severity=severity,
        description=f"{name} description",
        raw_output="{}",
        attempts=attempts,
        failures=failures,
    )


# ------------------------------------------------------------------
# parse_results tests
# ------------------------------------------------------------------

def test_parse_results_passthrough_when_no_run():
    state = _base_state()
    result = parse_results(state)
    assert result["run_result"] is None
    assert result["errors"] == []


def test_parse_results_warns_on_zero_probes():
    run = _make_run([])
    state = _base_state(run_result=run)
    result = parse_results(state)
    assert len(result["errors"]) == 1
    assert "zero probe results" in result["errors"][0]


def test_parse_results_clean_run():
    run = _make_run([_probe("DAN_11_0", ProbeStatus.PASSED)])
    state = _base_state(run_result=run)
    result = parse_results(state)
    assert result["errors"] == []


# ------------------------------------------------------------------
# prioritize tests
# ------------------------------------------------------------------

def test_prioritize_no_run():
    state = _base_state()
    result = prioritize(state)
    assert result["prioritized_findings"] == []


def test_prioritize_only_failures_included():
    run = _make_run([
        _probe("DAN_11_0", ProbeStatus.FAILED, Severity.HIGH),
        _probe("GCGCached", ProbeStatus.PASSED, Severity.MEDIUM),
        _probe("Encoding1", ProbeStatus.ERROR, Severity.LOW),
    ])
    state = _base_state(run_result=run)
    result = prioritize(state)
    findings = result["prioritized_findings"]
    assert len(findings) == 1
    assert findings[0].probe_name == "DAN_11_0"


def test_prioritize_regressions_rank_higher():
    run = _make_run([
        _probe("DAN_11_0", ProbeStatus.FAILED, Severity.HIGH, attempts=10, failures=1),
        _probe("GCGCached", ProbeStatus.FAILED, Severity.HIGH, attempts=10, failures=1),
    ])
    # DAN_11_0 is a regression, GCGCached is not
    regression = Regression(
        probe_category="dan",
        probe_name="DAN_11_0",
        severity=Severity.HIGH,
        description="",
        run_a_id="prev",
        run_b_id="curr",
        run_a_raw="",
        run_b_raw="",
    )
    state = _base_state(run_result=run, regressions=[regression])
    result = prioritize(state)
    findings = result["prioritized_findings"]
    assert findings[0].probe_name == "DAN_11_0"
    assert findings[0].is_regression is True
    assert findings[0].priority_score > findings[1].priority_score


def test_prioritize_critical_outranks_high_without_regression():
    run = _make_run([
        _probe("ProbeA", ProbeStatus.FAILED, Severity.HIGH, attempts=10, failures=5),
        _probe("ProbeB", ProbeStatus.FAILED, Severity.CRITICAL, attempts=10, failures=1),
    ])
    state = _base_state(run_result=run)
    result = prioritize(state)
    findings = result["prioritized_findings"]
    assert findings[0].probe_name == "ProbeB"
    assert findings[0].severity == Severity.CRITICAL


# ------------------------------------------------------------------
# graph routing tests (no LLM calls)
# ------------------------------------------------------------------

def test_graph_skips_patch_when_no_findings(tmp_path: Path, monkeypatch):
    """If all probes pass, graph should go run_probes→...→generate_report skipping patch."""
    from redteam.agent.graph import _after_prioritize

    state = _base_state(prioritized_findings=[])
    assert _after_prioritize(state) == "generate_report"


def test_graph_skips_retest_when_no_patches():
    from redteam.agent.graph import _after_suggest_patch

    state = _base_state(patches=[])
    assert _after_suggest_patch(state) == "generate_report"


def test_graph_routes_to_parse_when_run_succeeds():
    from redteam.agent.graph import _after_run_probes

    run = _make_run([])
    state = _base_state(run_result=run)
    assert _after_run_probes(state) == "parse_results"


def test_graph_routes_to_report_when_run_fails():
    from redteam.agent.graph import _after_run_probes

    state = _base_state(run_result=None)
    assert _after_run_probes(state) == "generate_report"
