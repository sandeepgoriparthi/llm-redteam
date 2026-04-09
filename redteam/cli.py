from __future__ import annotations

import sys
import logging
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich import box
from rich.panel import Panel
from rich.text import Text

from redteam.config import settings

app = typer.Typer(
    name="redteam",
    help="Continuous LLM red-team agent. Run probes, track regressions, generate reports.",
    add_completion=False,
)
console = Console()
err_console = Console(stderr=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


# ------------------------------------------------------------------
# Exit codes
# ------------------------------------------------------------------

class ExitCode:
    CLEAN = 0
    FAILURES = 1
    REGRESSIONS = 2
    RUN_ERROR = 3


# ------------------------------------------------------------------
# redteam run
# ------------------------------------------------------------------

@app.command()
def run(
    target: str = typer.Option(
        None,
        "--target",
        help="Provider: openai | ollama | custom. Overrides TARGET_PROVIDER in .env.",
    ),
    model: str = typer.Option(
        None,
        "--model",
        help="Model name. Overrides TARGET_MODEL in .env.",
    ),
    probes: Optional[str] = typer.Option(
        None,
        "--probes",
        help="Comma-separated probe categories. Default: dan,gcg,encoding,promptinject",
    ),
    system_prompt_file: Optional[Path] = typer.Option(
        None,
        "--system-prompt",
        exists=True,
        help="Path to a .txt file containing the system prompt to test.",
    ),
    no_patch: bool = typer.Option(
        False,
        "--no-patch",
        help="Skip LLM patch suggestion and retest (faster, no analyst API calls).",
    ),
) -> None:
    """
    Run a full red-team scan against the target LLM.

    Exits 0 (clean), 1 (failures), 2 (regressions), or 3 (run error).

    Examples:
        redteam run
        redteam run --target openai --model gpt-4o
        redteam run --probes dan,gcg --no-patch
        redteam run --system-prompt ./prompts/prod.txt
    """
    # Apply CLI overrides to settings at runtime
    if target:
        settings.target_provider = target  # type: ignore[assignment]
    if model:
        settings.target_model = model
    if probes:
        settings.garak_probe_categories = [p.strip() for p in probes.split(",")]

    system_prompt: str | None = None
    if system_prompt_file:
        system_prompt = system_prompt_file.read_text(encoding="utf-8").strip()

    from redteam.agent.graph import compiled_graph
    from redteam.agent.state import AgentState

    initial_state: AgentState = {
        "target_model": settings.target_model,
        "target_provider": settings.target_provider,
        "probe_categories": settings.garak_probe_categories,
        "system_prompt": system_prompt,
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

    # If --no-patch, swap in the skip-patch graph variant
    graph = compiled_graph
    if no_patch:
        from redteam.agent.graph import build_graph
        from redteam.agent.graph import _after_prioritize as _orig
        # Monkey-patch the conditional to always skip patching
        import redteam.agent.graph as _graph_mod
        _graph_mod._after_prioritize = lambda s: "generate_report"
        graph = build_graph()
        _graph_mod._after_prioritize = _orig

    console.print(
        Panel(
            f"[bold]Target:[/bold] {settings.target_model} ({settings.target_provider})\n"
            f"[bold]Probes:[/bold] {', '.join(settings.garak_probe_categories)}",
            title="[bold cyan]LLM Red-Team Run[/bold cyan]",
            expand=False,
        )
    )

    with console.status("[cyan]Running probes...[/cyan]", spinner="dots"):
        final_state = graph.invoke(initial_state)

    _print_run_summary(final_state)

    report_path = final_state.get("report_path", "")
    if report_path:
        console.print(f"\n[dim]Report: {report_path}[/dim]")

    # Exit code reflects the severity of findings
    errors = final_state.get("errors", [])
    regressions = final_state.get("regressions", [])
    run_result = final_state.get("run_result")

    if errors and not run_result:
        raise typer.Exit(ExitCode.RUN_ERROR)
    if regressions:
        raise typer.Exit(ExitCode.REGRESSIONS)
    if run_result and run_result.failed_probes:
        raise typer.Exit(ExitCode.FAILURES)
    raise typer.Exit(ExitCode.CLEAN)


# ------------------------------------------------------------------
# redteam report
# ------------------------------------------------------------------

@app.command()
def report(
    last: int = typer.Option(
        7,
        "--last",
        help="Show runs from the last N days.",
        min=1,
    ),
    model: Optional[str] = typer.Option(
        None,
        "--model",
        help="Filter by target model name.",
    ),
) -> None:
    """
    Show a summary table of recent runs.

    Examples:
        redteam report
        redteam report --last 14
        redteam report --last 7 --model gpt-4o
    """
    from redteam.db.repository import RunRepository

    repo = RunRepository(settings.db_path)
    try:
        runs = repo.runs_in_last_n_days(last)
        if model:
            runs = [r for r in runs if r.target_model == model]
    finally:
        repo.close()

    if not runs:
        console.print(f"[yellow]No runs found in the last {last} day(s).[/yellow]")
        raise typer.Exit(0)

    table = Table(
        title=f"Runs — last {last} day(s)",
        box=box.ROUNDED,
        show_lines=False,
    )
    table.add_column("Timestamp", style="dim", no_wrap=True)
    table.add_column("Run ID", style="dim", no_wrap=True)
    table.add_column("Model")
    table.add_column("Probes", justify="right")
    table.add_column("Failed", justify="right")
    table.add_column("Rate", justify="right")
    table.add_column("Duration", justify="right")

    for r in runs:
        probe_results = repo.get_probe_results(r.run_id) if runs else []

        passed = sum(1 for p in probe_results if p.passed)
        failed = sum(1 for p in probe_results if not p.passed)
        total = len(probe_results)
        rate = f"{passed/total*100:.0f}%" if total else "—"

        failed_style = "red bold" if failed else "green"

        table.add_row(
            r.timestamp.strftime("%Y-%m-%d %H:%M"),
            r.run_id[:12] + "…",
            r.target_model,
            str(total),
            Text(str(failed), style=failed_style),
            rate,
            f"{r.duration_seconds:.0f}s",
        )

    console.print(table)


# ------------------------------------------------------------------
# redteam diff
# ------------------------------------------------------------------

@app.command()
def diff(
    run_a: str = typer.Option(..., "--run-a", help="Baseline run ID."),
    run_b: str = typer.Option(..., "--run-b", help="Comparison run ID."),
) -> None:
    """
    Diff two runs and show regressions and fixes.

    Examples:
        redteam diff --run-a abc123 --run-b def456
    """
    from redteam.db.repository import RunRepository

    repo = RunRepository(settings.db_path)
    try:
        run_a_stored = repo.get_run(run_a)
        run_b_stored = repo.get_run(run_b)

        if not run_a_stored:
            err_console.print(f"[red]Run not found: {run_a}[/red]")
            raise typer.Exit(1)
        if not run_b_stored:
            err_console.print(f"[red]Run not found: {run_b}[/red]")
            raise typer.Exit(1)

        regressions, fixes = repo.diff(run_a, run_b)
    finally:
        repo.close()

    console.print(
        Panel(
            f"[bold]Baseline:[/bold] {run_a} ({run_a_stored.target_model})\n"
            f"[bold]Compare:[/bold]  {run_b} ({run_b_stored.target_model})",
            title="[bold cyan]Run Diff[/bold cyan]",
            expand=False,
        )
    )

    # Regressions
    if regressions:
        reg_table = Table(
            title=f"[bold red]Regressions ({len(regressions)})[/bold red]",
            box=box.SIMPLE,
        )
        reg_table.add_column("Probe")
        reg_table.add_column("Category")
        reg_table.add_column("Severity")
        for r in regressions:
            reg_table.add_row(
                r.probe_name,
                r.probe_category,
                Text(r.severity.value.upper(), style="red bold"),
            )
        console.print(reg_table)
    else:
        console.print("[green]No regressions.[/green]")

    # Fixes
    if fixes:
        fix_table = Table(
            title=f"[bold green]Fixes ({len(fixes)})[/bold green]",
            box=box.SIMPLE,
        )
        fix_table.add_column("Probe")
        fix_table.add_column("Category")
        fix_table.add_column("Severity")
        for f in fixes:
            fix_table.add_row(
                f.probe_name,
                f.probe_category,
                Text(f.severity.value.upper(), style="green"),
            )
        console.print(fix_table)
    else:
        console.print("[dim]No fixes.[/dim]")


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------

def _print_run_summary(state: dict) -> None:
    run = state.get("run_result")
    errors = state.get("errors", [])
    regressions = state.get("regressions", [])
    fixes = state.get("fixes", [])
    patches = state.get("patches", [])

    if errors and not run:
        console.print(f"\n[bold red]Run failed.[/bold red]")
        for e in errors:
            console.print(f"  [red]• {e}[/red]")
        return

    status_color = "green"
    status_label = "CLEAN"
    if regressions:
        status_color = "red"
        status_label = "REGRESSIONS DETECTED"
    elif run and run.failed_probes:
        status_color = "yellow"
        status_label = "FAILURES"

    console.print(f"\n[bold {status_color}]{status_label}[/bold {status_color}]")

    if run:
        table = Table(box=box.SIMPLE, show_header=False)
        table.add_column(style="dim", width=20)
        table.add_column()
        table.add_row("Total probes", str(run.total_probes))
        table.add_row("Passed", Text(str(len(run.passed_probes)), style="green"))
        table.add_row("Failed", Text(str(len(run.failed_probes)), style="red" if run.failed_probes else "green"))
        table.add_row("Success rate", f"{run.success_rate*100:.1f}%")
        table.add_row("Duration", f"{run.duration_seconds:.1f}s")
        if regressions:
            table.add_row("Regressions", Text(str(len(regressions)), style="red bold"))
        if fixes:
            table.add_row("Fixes", Text(str(len(fixes)), style="green"))
        if patches:
            confirmed = sum(1 for p in patches if p.confirmed)
            table.add_row("Patches", f"{confirmed}/{len(patches)} confirmed")
        console.print(table)

    if regressions:
        console.print("\n[bold red]Regressions:[/bold red]")
        for r in regressions:
            console.print(f"  [red]• {r.probe_category}/{r.probe_name} ({r.severity.value.upper()})[/red]")

    if errors:
        console.print("\n[yellow]Warnings:[/yellow]")
        for e in errors:
            console.print(f"  [yellow]• {e}[/yellow]")


if __name__ == "__main__":
    app()
