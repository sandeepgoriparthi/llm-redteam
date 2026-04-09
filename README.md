# llm-redteam

Continuous red-team agent for LLM systems. Runs Garak probes on a schedule, detects regressions, suggests system prompt patches, and generates daily reports.

---

## What it does

- Runs [Garak](https://github.com/NVIDIA/garak) vulnerability probes against any OpenAI, Ollama, or custom LLM endpoint
- Diffs every run against the previous one -- flags probes that newly fail (regressions)
- For each regression, calls an analyst LLM to suggest a targeted system prompt patch
- Re-tests each patch to confirm it actually blocks the attack
- Writes structured Markdown + JSON reports with severity scores and run history
- Stores all run history in SQLite for diffing any two runs at any point

---

## Stack

- Python 3.11+
- [Garak](https://github.com/NVIDIA/garak) 0.14.x -- LLM vulnerability scanner
- [LangGraph](https://github.com/langchain-ai/langgraph) -- agent orchestration
- SQLite -- run history and regression tracking
- [Typer](https://typer.tiangolo.com/) + [Rich](https://github.com/Textualize/rich) -- CLI
- Jinja2 -- report templating
- APScheduler -- cron scheduling (or Docker)

---

## Project structure

```
redteam/
  cli.py              # Typer CLI -- run, report, diff commands
  config.py           # Pydantic settings, reads from .env
  runner/
    garak_runner.py   # Garak subprocess wrapper with retries
    models.py         # ProbeResult, RunResult dataclasses
  db/
    schema.py         # SQLite schema + migrations
    repository.py     # All DB access + diff logic
    models.py         # DB-layer dataclasses
  agent/
    state.py          # AgentState TypedDict + intermediate types
    graph.py          # LangGraph StateGraph with conditional edges
    nodes/            # One file per node: run_probes → parse → compare
                      #   → prioritize → suggest_patch → retest → report
  reports/
    generator.py      # ReportData assembly + file writing
    templates/        # Jinja2 report template
  scheduler.py        # APScheduler for cron-based runs
tests/                # 30 tests -- db, agent, reports, runner
```

---

## Setup

```bash
git clone https://github.com/sandeepgoriparthi/llm-redteam
cd llm-redteam
pip install -e .
cp .env.example .env
# edit .env and add your OPENAI_API_KEY
```

---

## Usage

```bash
# Run a full scan
redteam run --target openai --model gpt-4o

# Skip patch suggestions (faster, no analyst API cost)
redteam run --target openai --model gpt-4o --no-patch

# Run against local Ollama
redteam run --target ollama --model llama3

# Test a specific system prompt
redteam run --target openai --model gpt-4o --system-prompt ./prompts/prod.txt

# Show last 7 days of runs
redteam report --last 7

# Diff two runs
redteam diff --run-a <run-id> --run-b <run-id>
```

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | All probes passed |
| 1 | Failures detected |
| 2 | Regressions detected (new failures vs baseline) |
| 3 | Run error |

CI can branch on these directly.

---

## Scheduling

**Docker (recommended):**
```bash
# Runs on schedule defined by SCHEDULE_CRON in .env (default: 2am daily)
docker compose up -d

# One-shot manual run
docker compose run --rm scan
```

**APScheduler (no Docker):**
```bash
SCHEDULE_CRON="0 2 * * *" python -m redteam.scheduler
```

---

## Agent graph

```
run_probes → parse_results → compare_baseline → prioritize
    → suggest_patch → retest → generate_report
```

Short-circuits to `generate_report` if: run fails, zero probes returned, no failures, or no patches generated. You always get a report.

---

## Reports

Each run writes two files to `reports/`:
- `report_YYYYMMDD_HHMMSS.md` -- human-readable with regression callouts and patch recommendations
- `report_YYYYMMDD_HHMMSS.json` -- structured for downstream tooling

---

## Tests

```bash
pytest tests/ -v
```

30 tests covering DB diff logic, agent node routing, report generation, and runner parsing.
