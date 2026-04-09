from __future__ import annotations

import logging

from redteam.agent.state import AgentState, PatchSuggestion, RetestResult
from redteam.config import settings
from redteam.db.repository import RunRepository
from redteam.runner.garak_runner import GarakRunError, GarakRunner
from redteam.runner.models import ProbeStatus

logger = logging.getLogger(__name__)


def _build_patched_prompt(base_prompt: str | None, patch_text: str) -> str:
    """Append the patch to the existing system prompt."""
    if base_prompt:
        return f"{base_prompt}\n\n{patch_text}"
    return patch_text


def retest(state: AgentState) -> AgentState:
    """
    Node 6: Re-run only the probes that were patched, using the patched prompt.

    This does NOT re-run the full suite -- only the specific probes we generated
    patches for. That keeps retest fast (seconds vs. minutes).

    Each patch is tested independently so we know exactly which patches
    confirmed vs. which failed to block the probe.

    Tradeoff: running patches independently means we don't catch interactions
    between patches. That's acceptable for v1 -- combined-patch testing is v2.
    """
    patches = state.get("patches", [])
    if not patches:
        logger.info("No patches to retest -- skipping retest node")
        return {**state, "retest_results": []}

    runner = GarakRunner(settings)
    repo = RunRepository(settings.db_path)
    retest_results: list[RetestResult] = []
    confirmed_patches: list[PatchSuggestion] = []

    for patch in patches:
        patched_prompt = _build_patched_prompt(
            state.get("system_prompt"),
            patch.patch_text,
        )
        probe_spec = [patch.probe_category]

        logger.info(
            "Retesting %s/%s with patched prompt",
            patch.probe_category,
            patch.probe_name,
        )

        try:
            run_result = runner.run(
                probe_categories=probe_spec,
                system_prompt=patched_prompt,
            )
            repo.save_run(run_result)

            # Find the specific probe result from the retest run
            matching = [
                pr for pr in run_result.probe_results
                if pr.probe_name == patch.probe_name
            ]

            if not matching:
                logger.warning(
                    "Probe %s not found in retest run -- may have been renamed",
                    patch.probe_name,
                )
                retest_results.append(RetestResult(
                    probe_category=patch.probe_category,
                    probe_name=patch.probe_name,
                    patch_text=patch.patch_text,
                    passed=False,
                    run_id=run_result.run_id,
                ))
                continue

            probe_passed = matching[0].status == ProbeStatus.PASSED
            retest_results.append(RetestResult(
                probe_category=patch.probe_category,
                probe_name=patch.probe_name,
                patch_text=patch.patch_text,
                passed=probe_passed,
                run_id=run_result.run_id,
            ))

            if probe_passed:
                patch.confirmed = True
                confirmed_patches.append(patch)
                logger.info("Patch CONFIRMED for %s/%s", patch.probe_category, patch.probe_name)
            else:
                logger.warning(
                    "Patch did NOT fix %s/%s -- probe still failing",
                    patch.probe_category,
                    patch.probe_name,
                )

        except GarakRunError as exc:
            msg = f"Retest failed for {patch.probe_name}: {exc}"
            logger.error(msg)
            state.setdefault("errors", []).append(msg)

    repo.close()

    logger.info(
        "Retest complete | patches=%d confirmed=%d failed=%d",
        len(patches),
        len(confirmed_patches),
        len(patches) - len(confirmed_patches),
    )

    return {**state, "retest_results": retest_results, "patches": patches}
