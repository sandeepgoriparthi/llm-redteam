from __future__ import annotations

import logging

from langgraph.graph import END, START, StateGraph

from redteam.agent.state import AgentState
from redteam.agent.nodes.run_probes import run_probes
from redteam.agent.nodes.parse_results import parse_results
from redteam.agent.nodes.compare_baseline import compare_baseline
from redteam.agent.nodes.prioritize import prioritize
from redteam.agent.nodes.suggest_patch import suggest_patch
from redteam.agent.nodes.retest import retest
from redteam.agent.nodes.generate_report import generate_report

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Conditional edge functions
# ------------------------------------------------------------------

def _after_run_probes(state: AgentState) -> str:
    """
    If run_probes failed (run_result is None), skip straight to report.
    The error is already in state["errors"].
    """
    if state.get("run_result") is None:
        logger.warning("run_probes produced no result -- routing to generate_report")
        return "generate_report"
    return "parse_results"


def _after_parse_results(state: AgentState) -> str:
    """
    If parse found zero probe results, skip diff and patching -- nothing to analyse.
    """
    run = state.get("run_result")
    if run is None or run.total_probes == 0:
        logger.warning("No probe results to analyse -- routing to generate_report")
        return "generate_report"
    return "compare_baseline"


def _after_prioritize(state: AgentState) -> str:
    """
    If there are no actionable findings, skip LLM patch generation.
    This avoids unnecessary API calls when everything passed.
    """
    findings = state.get("prioritized_findings", [])
    if not findings:
        logger.info("No failed probes -- skipping suggest_patch and retest")
        return "generate_report"
    return "suggest_patch"


def _after_suggest_patch(state: AgentState) -> str:
    """
    If no patches were generated (e.g. all failures were LOW/INFO severity,
    or LLM calls all failed), skip retest.
    """
    patches = state.get("patches", [])
    if not patches:
        logger.info("No patches generated -- skipping retest")
        return "generate_report"
    return "retest"


# ------------------------------------------------------------------
# Graph factory
# ------------------------------------------------------------------

def build_graph() -> StateGraph:
    """
    Build and compile the red-team agent graph.

    Flow (happy path):
        run_probes → parse_results → compare_baseline → prioritize
        → suggest_patch → retest → generate_report

    Short-circuit paths:
        run_probes failure           → generate_report
        zero probe results           → generate_report
        no failed probes             → generate_report (skips patch + retest)
        no patches generated         → generate_report (skips retest)
    """
    graph = StateGraph(AgentState)

    # Register nodes
    graph.add_node("run_probes", run_probes)
    graph.add_node("parse_results", parse_results)
    graph.add_node("compare_baseline", compare_baseline)
    graph.add_node("prioritize", prioritize)
    graph.add_node("suggest_patch", suggest_patch)
    graph.add_node("retest", retest)
    graph.add_node("generate_report", generate_report)

    # Entry point
    graph.add_edge(START, "run_probes")

    # Conditional edges
    graph.add_conditional_edges(
        "run_probes",
        _after_run_probes,
        {"parse_results": "parse_results", "generate_report": "generate_report"},
    )
    graph.add_conditional_edges(
        "parse_results",
        _after_parse_results,
        {"compare_baseline": "compare_baseline", "generate_report": "generate_report"},
    )
    graph.add_conditional_edges(
        "prioritize",
        _after_prioritize,
        {"suggest_patch": "suggest_patch", "generate_report": "generate_report"},
    )
    graph.add_conditional_edges(
        "suggest_patch",
        _after_suggest_patch,
        {"retest": "retest", "generate_report": "generate_report"},
    )

    # Unconditional edges
    graph.add_edge("compare_baseline", "prioritize")
    graph.add_edge("retest", "generate_report")
    graph.add_edge("generate_report", END)

    return graph.compile()


# Module-level compiled graph -- import this in cli.py
compiled_graph = build_graph()
