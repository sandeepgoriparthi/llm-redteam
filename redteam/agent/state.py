from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypedDict

from redteam.db.models import Fix, Regression
from redteam.runner.models import RunResult, Severity


# ------------------------------------------------------------------
# Intermediate types used only inside the agent graph
# ------------------------------------------------------------------

@dataclass
class PrioritizedFinding:
    """
    A failed probe with a computed priority score.
    Regressions (new failures vs baseline) are ranked above persistent failures.
    """
    probe_category: str
    probe_name: str
    severity: Severity
    description: str
    is_regression: bool     # True if it passed in the previous run
    failure_rate: float     # failures / attempts from this run
    priority_score: int     # computed in prioritize node, higher = more urgent


@dataclass
class PatchSuggestion:
    """LLM-generated system prompt addition to block a failing probe."""
    probe_category: str
    probe_name: str
    severity: Severity
    original_description: str
    patch_text: str         # the suggested system prompt addition
    rationale: str          # why this patch should work
    confirmed: bool = False # set to True after retest passes


@dataclass
class RetestResult:
    """Outcome of re-running a single probe against a patched system prompt."""
    probe_category: str
    probe_name: str
    patch_text: str
    passed: bool
    run_id: str             # the retest run_id stored in DB


# ------------------------------------------------------------------
# Agent state -- single TypedDict passed between all nodes
# ------------------------------------------------------------------

class AgentState(TypedDict):
    # --- Inputs (set before graph starts) ---
    target_model: str
    target_provider: str
    probe_categories: list[str]
    system_prompt: str | None       # current production system prompt

    # --- After run_probes ---
    run_result: RunResult | None

    # --- After compare_baseline ---
    baseline_run_id: str | None
    regressions: list[Regression]
    fixes: list[Fix]

    # --- After prioritize ---
    prioritized_findings: list[PrioritizedFinding]

    # --- After suggest_patch ---
    patches: list[PatchSuggestion]

    # --- After retest ---
    retest_results: list[RetestResult]

    # --- After generate_report ---
    report_path: str
    report_json: dict

    # --- Errors accumulated across nodes (non-fatal) ---
    errors: list[str]
