from __future__ import annotations

import logging

from redteam.agent.state import AgentState
from redteam.config import settings
from redteam.reports.generator import ReportGenerator, build_report_data

logger = logging.getLogger(__name__)


def generate_report(state: AgentState) -> AgentState:
    """
    Node 7: Assemble report data from state, render Markdown + JSON.
    Delegates all logic to ReportGenerator -- this node is intentionally thin.
    """
    report_data = build_report_data(state)
    generator = ReportGenerator(settings.reports_dir)
    md_path, json_path = generator.write(report_data)

    logger.info(
        "Reports written | status=%s regressions=%d fixes=%d patches=%d",
        report_data.overall_status,
        len(report_data.regressions),
        len(report_data.fixes),
        len(report_data.patches),
    )

    return {
        **state,
        "report_path": str(md_path),
        "report_json": report_data.to_dict(),
    }
