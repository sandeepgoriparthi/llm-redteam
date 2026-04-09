from __future__ import annotations

import logging

from redteam.agent.state import AgentState

logger = logging.getLogger(__name__)


def parse_results(state: AgentState) -> AgentState:
    """
    Node 2: Validate and summarise the run_result.

    The runner already parses garak output into typed dataclasses -- this node
    doesn't re-parse. Its job is to log a human-readable summary and catch any
    runs that completed but produced zero results (usually a garak probe_spec
    format change or a network issue against the target).
    """
    run = state.get("run_result")
    if run is None:
        # run_probes already logged the error; nothing to do here
        return state

    total = run.total_probes
    failed = len(run.failed_probes)
    errored = len(run.errored_probes)
    passed = len(run.passed_probes)

    logger.info(
        "Parse summary | total=%d passed=%d failed=%d errored=%d success_rate=%.1f%%",
        total, passed, failed, errored, run.success_rate * 100,
    )

    if total == 0:
        msg = (
            f"Run {run.run_id} completed with exit_code=0 but returned zero probe "
            f"results. Most likely cause: --probe_spec format changed in garak "
            f"{run.garak_version}. Check _build_garak_command() in garak_runner.py."
        )
        logger.warning(msg)
        return {**state, "errors": state.get("errors", []) + [msg]}

    if errored > 0:
        logger.warning(
            "%d probe(s) errored out and will be excluded from diff and prioritization.",
            errored,
        )

    return state
