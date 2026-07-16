#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DATABASE="data/measurement.sqlite"
EXPORT_DIR="exports"
SERVICE_NAME="measurement_system.service"
SERVICE_WAS_ACTIVE=false

cd "${PROJECT_DIR}"

if sudo systemctl is-active --quiet "${SERVICE_NAME}"; then
    SERVICE_WAS_ACTIVE=true
    sudo systemctl stop "${SERVICE_NAME}"
fi

restart_service() {
    if [[ "${SERVICE_WAS_ACTIVE}" == "true" ]]; then
        sudo systemctl start "${SERVICE_NAME}"
    fi
}
trap restart_service EXIT

if [[ ! -f "${DATABASE}" ]]; then
    echo "Keine Datenbank gefunden: ${PROJECT_DIR}/${DATABASE}" >&2
    exit 1
fi

# Übernimmt noch vorhandene WAL-Daten in die eigentliche SQLite-Datei.
sqlite3 "${DATABASE}" "PRAGMA wal_checkpoint(TRUNCATE);" >/dev/null

mkdir -p "${EXPORT_DIR}"

python3 - <<'PY'
from __future__ import annotations

import gzip
import re
import shutil
import sqlite3
from pathlib import Path


DATABASE = Path("data/measurement.sqlite")
EXPORT_DIR = Path("exports")
TABLES_BY_RUN_ID = [
    "startup_snapshots",
    "samples_1hz",
    "ping_results",
    "iperf_results",
    "error_log",
    "system_events",
]


def safe_timestamp(value: str | None) -> str:
    if not value:
        return "unknown-time"
    return re.sub(r"[^0-9A-Za-z]+", "-", value).strip("-")


def create_schema(source: sqlite3.Connection, target: sqlite3.Connection) -> None:
    rows = source.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
          AND sql IS NOT NULL
        ORDER BY name
        """
    ).fetchall()
    for (sql,) in rows:
        target.execute(sql)


def copy_rows(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    table: str,
    where_sql: str,
    parameters: tuple[object, ...],
) -> int:
    columns = [row[1] for row in source.execute(f"PRAGMA table_info({table})")]
    if not columns:
        return 0
    placeholders = ", ".join(["?"] * len(columns))
    quoted_columns = ", ".join(columns)
    rows = source.execute(
        f"SELECT {quoted_columns} FROM {table} WHERE {where_sql}",
        parameters,
    ).fetchall()
    if not rows:
        return 0
    target.executemany(
        f"INSERT INTO {table} ({quoted_columns}) VALUES ({placeholders})",
        rows,
    )
    return len(rows)


def export_run(source: sqlite3.Connection, run: sqlite3.Row) -> Path:
    run_id = int(run["run_id"])
    start_time = safe_timestamp(run["start_time_system_utc"])
    final_path = EXPORT_DIR / f"run_{run_id:06d}_{start_time}.sqlite.gz"
    temp_sqlite = EXPORT_DIR / f".run_{run_id:06d}.sqlite"

    if final_path.exists():
        return final_path

    if temp_sqlite.exists():
        temp_sqlite.unlink()

    target = sqlite3.connect(temp_sqlite)
    try:
        create_schema(source, target)
        with target:
            copy_rows(source, target, "measurement_runs", "run_id = ?", (run_id,))
            for table in TABLES_BY_RUN_ID:
                copy_rows(source, target, table, "run_id = ?", (run_id,))
        target.execute("VACUUM")
    finally:
        target.close()

    with temp_sqlite.open("rb") as src, gzip.open(final_path, "wb", compresslevel=9) as dst:
        shutil.copyfileobj(src, dst)
    temp_sqlite.unlink()
    return final_path


def main() -> None:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(DATABASE)
    source.row_factory = sqlite3.Row
    try:
        runs = source.execute(
            "SELECT * FROM measurement_runs ORDER BY run_id"
        ).fetchall()
        if not runs:
            print("Keine Messfahrten in der Datenbank gefunden.")
            return

        created = []
        for run in runs:
            run_id = int(run["run_id"])
            if list(EXPORT_DIR.glob(f"run_{run_id:06d}_*.sqlite.gz")):
                continue
            created.append(export_run(source, run))

        if not created:
            print("Keine neuen Messfahrten zum Exportieren gefunden.")
            return

        for path in created:
            size_mib = path.stat().st_size / 1024 / 1024
            print(f"Export erzeugt: {path} ({size_mib:.2f} MiB)")
            if path.stat().st_size >= 100 * 1024 * 1024:
                raise SystemExit(
                    f"{path} ist mindestens 100 MiB groß und für reguläres GitHub-Git zu groß."
                )
            if path.stat().st_size >= 50 * 1024 * 1024:
                print(f"Warnung: {path} ist mindestens 50 MiB groß.")
    finally:
        source.close()


if __name__ == "__main__":
    main()
PY

git add -- "${EXPORT_DIR}"
if git diff --cached --quiet -- "${EXPORT_DIR}"; then
    echo "Keine neuen Exportdateien zum Committen vorhanden."
    echo "Falls bereits ein lokaler Commit wartet: git push origin HEAD"
    exit 0
fi

git commit -m "Messdatenexport $(date --utc +'%Y-%m-%dT%H:%M:%SZ')" -- "${EXPORT_DIR}"
git push origin HEAD

echo "Messdatenexporte wurden erfolgreich zu GitHub übertragen."
