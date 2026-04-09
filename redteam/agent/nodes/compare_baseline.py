from __future__ import annotations

import logging

from redteam.agent.state import AgentState
from redteam.config import settings
from redteam.db.repository import RunRepository

logger = logging.getLogger(__name__)


def compare_baseline(state: AgentState) -> AgentState:
    """
    Node 3: Diff the current run against the most recent previous run.

    If no baseline exists (first-ever run), regressions and fixes are empty
    and the agent continues to prioritize all failures as new findings.
    """
    run = state.get("run_result")
    if run is None:
        return state

    repo = RunRepository(settings.db_path)
    try:
        # Fetch latest run that is NOT the current one
        all_runs = repo.list_runs(target_model=run.target_model, limit=10)
        previous_runs = [r for r in all_runs if r.run_id != run.run_id]

        if not previous_runs:
            logger.info(
                "No baseline found for model=%s -- this is the first run. "
                "All failures treated as new findings.",
                run.target_model,
            )
            return {
                **state,
                "baseline_run_id": None,
                "regressions": [],
                "fixes": [],
            }

        baseline = previous_runs[0]
        logger.info(
            "Diffing run=%s against baseline=%s",
            run.run_id,
            baseline.run_id,
        )

        regressions, fixes = repo.diff(baseline.run_id, run.run_id)

        logger.info(
            "Diff complete | regressions=%d fixes=%d",
            len(regressions),
            len(fixes),
        )

        return {
            **state,
            "baseline_run_id": baseline.run_id,
            "regressions": regressions,
            "fixes": fixes,
        }

    finally:
        repo.close()
