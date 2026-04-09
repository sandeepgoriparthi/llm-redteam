from __future__ import annotations

import logging

from redteam.agent.state import AgentState, PrioritizedFinding
from redteam.runner.models import ProbeStatus, Severity

logger = logging.getLogger(__name__)

# Severity score -- higher is more urgent
_SEVERITY_SCORE: dict[Severity, int] = {
    Severity.CRITICAL: 100,
    Severity.HIGH:     75,
    Severity.MEDIUM:   50,
    Severity.LOW:      25,
    Severity.INFO:     5,
    Severity.UNKNOWN:  1,
}

# Regression bonus -- a new failure is more urgent than a persistent one
_REGRESSION_BONUS = 30


def prioritize(state: AgentState) -> AgentState:
    """
    Node 4: Score and rank all failed probes from the current run.

    Priority score = severity_score + regression_bonus (if applicable)
                   + failure_rate * 10

    Regressions (probes that passed last run but fail now) get the bonus
    because they signal something changed -- a code push, a prompt edit,
    a model update -- and need immediate attention.
    """
    run = state.get("run_result")
    if run is None:
        return {**state, "prioritized_findings": []}

    regression_keys = {
        (r.probe_category, r.probe_name)
        for r in state.get("regressions", [])
    }

    findings: list[PrioritizedFinding] = []

    for probe in run.probe_results:
        if probe.status != ProbeStatus.FAILED:
            continue

        is_regression = (probe.probe_category, probe.probe_name) in regression_keys
        score = (
            _SEVERITY_SCORE.get(probe.severity, 1)
            + (_REGRESSION_BONUS if is_regression else 0)
            + int(probe.failure_rate * 10)
        )

        findings.append(
            PrioritizedFinding(
                probe_category=probe.probe_category,
                probe_name=probe.probe_name,
                severity=probe.severity,
                description=probe.description,
                is_regression=is_regression,
                failure_rate=probe.failure_rate,
                priority_score=score,
            )
        )

    findings.sort(key=lambda f: f.priority_score, reverse=True)

    logger.info(
        "Prioritized %d failed probes (%d regressions)",
        len(findings),
        sum(1 for f in findings if f.is_regression),
    )

    if findings:
        top = findings[0]
        logger.info(
            "Top finding: %s/%s severity=%s regression=%s score=%d",
            top.probe_category, top.probe_name,
            top.severity.value, top.is_regression, top.priority_score,
        )

    return {**state, "prioritized_findings": findings}
