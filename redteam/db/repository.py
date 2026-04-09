from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from redteam.db.models import Fix, Regression, StoredProbeResult, StoredRun
from redteam.db.schema import connect, migrate
from redteam.runner.models import RunResult, Severity


class RunRepository:
    """
    All DB access lives here. No SQL outside this class.

    Usage:
        repo = RunRepository(Path("data/redteam.db"))
        repo.save_run(run_result)
        regressions = repo.diff(run_id_a, run_id_b)
    """

    def __init__(self, db_path: Path) -> None:
        self._conn = connect(db_path)
        migrate(self._conn)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def save_run(self, run: RunResult) -> None:
        """Persist a full RunResult (run metadata + all probe results)."""
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO runs (
                    run_id, timestamp, target_model, target_provider,
                    probe_categories, garak_version, duration_seconds,
                    exit_code, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.timestamp.isoformat(),
                    run.target_model,
                    run.target_provider,
                    json.dumps(run.probe_categories),
                    run.garak_version,
                    run.duration_seconds,
                    run.exit_code,
                    run.error_message,
                ),
            )
            self._conn.executemany(
                """
                INSERT INTO probe_results (
                    run_id, probe_category, probe_name, status, severity,
                    description, attempts, failures, raw_output
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run.run_id,
                        pr.probe_category,
                        pr.probe_name,
                        pr.status.value,
                        pr.severity.value,
                        pr.description,
                        pr.attempts,
                        pr.failures,
                        pr.raw_output,
                    )
                    for pr in run.probe_results
                ],
            )

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_run(self, run_id: str) -> StoredRun | None:
        row = self._conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return _row_to_stored_run(row) if row else None

    def list_runs(
        self,
        target_model: str | None = None,
        limit: int = 50,
    ) -> list[StoredRun]:
        if target_model:
            rows = self._conn.execute(
                "SELECT * FROM runs WHERE target_model = ? "
                "ORDER BY timestamp DESC LIMIT ?",
                (target_model, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM runs ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_stored_run(r) for r in rows]

    def get_latest_run_id(self, target_model: str | None = None) -> str | None:
        if target_model:
            row = self._conn.execute(
                "SELECT run_id FROM runs WHERE target_model = ? "
                "ORDER BY timestamp DESC LIMIT 1",
                (target_model,),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT run_id FROM runs ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
        return row["run_id"] if row else None

    def get_probe_results(self, run_id: str) -> list[StoredProbeResult]:
        rows = self._conn.execute(
            "SELECT * FROM probe_results WHERE run_id = ?", (run_id,)
        ).fetchall()
        return [_row_to_stored_probe(r) for r in rows]

    def runs_in_last_n_days(self, days: int) -> list[StoredRun]:
        rows = self._conn.execute(
            """
            SELECT * FROM runs
            WHERE timestamp >= datetime('now', ?)
            ORDER BY timestamp DESC
            """,
            (f"-{days} days",),
        ).fetchall()
        return [_row_to_stored_run(r) for r in rows]

    # ------------------------------------------------------------------
    # Diff -- the core regression detection logic
    # ------------------------------------------------------------------

    def diff(self, run_id_a: str, run_id_b: str) -> tuple[list[Regression], list[Fix]]:
        """
        Compare two runs and return regressions and fixes.

        Regression: probe passed in run_a, failed in run_b  (new vulnerability)
        Fix:        probe failed in run_a, passed in run_b  (resolved vulnerability)

        Probes that were error/skipped in either run are excluded from the diff
        to avoid false positives from infrastructure noise.

        Tradeoff: we join on (probe_category, probe_name). If garak renames
        a probe between versions, it will appear as a new failure rather than
        a regression. This is intentional -- a renamed probe is a different
        probe until proven otherwise.
        """
        sql = """
        SELECT
            a.probe_category,
            a.probe_name,
            a.status     AS status_a,
            a.severity   AS severity_a,
            a.description AS description_a,
            a.raw_output  AS raw_a,
            b.status     AS status_b,
            b.severity   AS severity_b,
            b.raw_output  AS raw_b
        FROM probe_results a
        JOIN probe_results b
            ON  a.probe_category = b.probe_category
            AND a.probe_name     = b.probe_name
        WHERE a.run_id = ?
          AND b.run_id = ?
          AND a.status NOT IN ('error', 'skipped')
          AND b.status NOT IN ('error', 'skipped')
        """
        rows = self._conn.execute(sql, (run_id_a, run_id_b)).fetchall()

        regressions: list[Regression] = []
        fixes: list[Fix] = []

        for row in rows:
            passed_in_a = row["status_a"] == "passed"
            passed_in_b = row["status_b"] == "passed"

            if passed_in_a and not passed_in_b:
                regressions.append(
                    Regression(
                        probe_category=row["probe_category"],
                        probe_name=row["probe_name"],
                        severity=_parse_severity(row["severity_b"]),
                        description=row["description_a"],
                        run_a_id=run_id_a,
                        run_b_id=run_id_b,
                        run_a_raw=row["raw_a"],
                        run_b_raw=row["raw_b"],
                    )
                )
            elif not passed_in_a and passed_in_b:
                fixes.append(
                    Fix(
                        probe_category=row["probe_category"],
                        probe_name=row["probe_name"],
                        severity=_parse_severity(row["severity_a"]),
                        run_a_id=run_id_a,
                        run_b_id=run_id_b,
                    )
                )

        return regressions, fixes

    def close(self) -> None:
        self._conn.close()


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------

def _row_to_stored_run(row: sqlite3.Row) -> StoredRun:
    return StoredRun(
        run_id=row["run_id"],
        timestamp=datetime.fromisoformat(row["timestamp"]),
        target_model=row["target_model"],
        target_provider=row["target_provider"],
        probe_categories=json.loads(row["probe_categories"]),
        garak_version=row["garak_version"],
        duration_seconds=row["duration_seconds"],
        exit_code=row["exit_code"],
        error_message=row["error_message"],
    )


def _row_to_stored_probe(row: sqlite3.Row) -> StoredProbeResult:
    return StoredProbeResult(
        id=row["id"],
        run_id=row["run_id"],
        probe_category=row["probe_category"],
        probe_name=row["probe_name"],
        status=row["status"],
        severity=row["severity"],
        description=row["description"],
        attempts=row["attempts"],
        failures=row["failures"],
        raw_output=row["raw_output"],
    )


def _parse_severity(value: str) -> Severity:
    try:
        return Severity(value)
    except ValueError:
        return Severity.UNKNOWN
