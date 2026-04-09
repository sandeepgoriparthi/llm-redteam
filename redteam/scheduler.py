from __future__ import annotations

"""
APScheduler-based scheduler for running red-team scans on a cron schedule.
Use this when you're not running via Docker.

Usage:
    python -m redteam.scheduler

The schedule is configured via environment variables:
    SCHEDULE_CRON    cron expression (default: "0 2 * * *" = 2am daily)
    SCHEDULE_PROBES  comma-separated probe categories (default: from settings)
"""

import logging
import os
import sys

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from redteam.config import settings

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

DEFAULT_CRON = "0 2 * * *"   # 2am daily


def run_scheduled_scan() -> None:
    """Called by the scheduler on each trigger. Runs the full agent pipeline."""
    from redteam.agent.graph import compiled_graph
    from redteam.agent.state import AgentState

    logger.info("Scheduled scan starting | model=%s", settings.target_model)

    initial_state: AgentState = {
        "target_model": settings.target_model,
        "target_provider": settings.target_provider,
        "probe_categories": settings.garak_probe_categories,
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

    try:
        final_state = compiled_graph.invoke(initial_state)
        regressions = final_state.get("regressions", [])
        report_path = final_state.get("report_path", "")

        if regressions:
            logger.warning(
                "Scheduled scan complete -- %d regression(s) detected. Report: %s",
                len(regressions),
                report_path,
            )
        else:
            logger.info(
                "Scheduled scan complete -- no regressions. Report: %s",
                report_path,
            )
    except Exception:
        logger.exception("Scheduled scan raised an unhandled exception")


def main() -> None:
    cron_expr = os.getenv("SCHEDULE_CRON", DEFAULT_CRON)
    logger.info("Starting scheduler | cron='%s' model=%s", cron_expr, settings.target_model)

    # Validate the cron expression before blocking
    try:
        trigger = CronTrigger.from_crontab(cron_expr)
    except ValueError as exc:
        logger.error("Invalid SCHEDULE_CRON expression '%s': %s", cron_expr, exc)
        sys.exit(1)

    scheduler = BlockingScheduler()
    scheduler.add_job(run_scheduled_scan, trigger=trigger, id="redteam_scan")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")


if __name__ == "__main__":
    main()
