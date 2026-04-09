from __future__ import annotations

import logging

from redteam.agent.state import AgentState
from redteam.config import settings
from redteam.db.repository import RunRepository
from redteam.runner.garak_runner import GarakRunError, GarakRunner

logger = logging.getLogger(__name__)


def run_probes(state: AgentState) -> AgentState:
    """
    Node 1: Execute garak against the target, persist results to DB.

    On success:  state["run_result"] is populated.
    On failure:  error is appended to state["errors"], run_result stays None.
                 The graph will short-circuit to generate_report.
    """
    runner = GarakRunner(settings)
    repo = RunRepository(settings.db_path)

    try:
        logger.info(
            "Starting garak run | model=%s provider=%s probes=%s",
            state["target_model"],
            state["target_provider"],
            state["probe_categories"],
        )
        run_result = runner.run(
            probe_categories=state["probe_categories"],
            system_prompt=state.get("system_prompt"),
        )
        repo.save_run(run_result)

        logger.info(
            "Run complete | run_id=%s total=%d failed=%d duration=%.1fs",
            run_result.run_id,
            run_result.total_probes,
            len(run_result.failed_probes),
            run_result.duration_seconds,
        )
        return {**state, "run_result": run_result}

    except GarakRunError as exc:
        msg = f"run_probes failed after retries: {exc}"
        logger.error(msg)
        return {**state, "errors": state.get("errors", []) + [msg]}

    finally:
        repo.close()
