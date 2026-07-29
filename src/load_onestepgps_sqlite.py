import argparse
import csv
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIR = PROJECT_ROOT / "data" / "onestepgps_reports"
DEFAULT_OUTPUT_DB = PROJECT_ROOT / "data" / "flh_insure_audit.db"
TABLE_NAME = "drives_and_stops"
LOAD_MANIFEST_TABLE = "onestepgps_load_manifest"
EDT = timezone(-timedelta(hours=4), name="EDT")
TIMESTAMP_COLUMNS = {"start_time", "end_time"}


def _sqlite_identifier(name):
    if not name:
        raise ValueError(f"Unsafe SQLite identifier: {name}")
    escaped_name = name.replace('"', '""')
    return f'"{escaped_name}"'


def _normalize_column_name(name):
    normalized = name.strip().lower()
    normalized = re.sub(r"\(([^)]+)\)", r"_\1", normalized)
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized)
    return normalized.strip("_")


def _parse_edt_timestamp(value):
    value = (value or "").strip()
    if not value or value.lower() == "no data":
        return ""

    parsed = datetime.strptime(value, "%m/%d/%Y %I:%M:%S %p")
    return parsed.replace(tzinfo=EDT).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _csv_reports(input_dir):
    reports = sorted(Path(input_dir).glob("*.csv"))
    if not reports:
        raise FileNotFoundError(f"No OneStepGPS CSV reports found in {input_dir}")
    return reports


def _read_report_rows(csv_path):
    with Path(csv_path).open(encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        if reader.fieldnames is None:
            return [], []

        columns = [_normalize_column_name(column) for column in reader.fieldnames]
        if any(not column for column in columns):
            raise ValueError(f"{csv_path} contains blank column names")

        rows = []
        for row_number, raw_row in enumerate(reader, start=2):
            row = {
                columns[index]: (raw_row.get(original_column) or "").strip()
                for index, original_column in enumerate(reader.fieldnames)
            }

            for column in TIMESTAMP_COLUMNS:
                if column in row:
                    row[column] = _parse_edt_timestamp(row[column])

            row["source_file"] = Path(csv_path).name
            row["source_row_number"] = str(row_number)
            rows.append(row)

    return columns + ["source_file", "source_row_number"], rows


def _create_table(connection, columns):
    quoted_table = _sqlite_identifier(TABLE_NAME)
    quoted_columns = ", ".join(f"{_sqlite_identifier(column)} TEXT" for column in columns)

    connection.execute(f"DROP TABLE IF EXISTS {quoted_table}")
    connection.execute(f"CREATE TABLE {quoted_table} ({quoted_columns})")


def _insert_rows(connection, columns, rows):
    if not rows:
        return 0

    quoted_table = _sqlite_identifier(TABLE_NAME)
    quoted_columns = ", ".join(_sqlite_identifier(column) for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    values = ([row.get(column, "") for column in columns] for row in rows)

    connection.executemany(
        f"INSERT INTO {quoted_table} ({quoted_columns}) VALUES ({placeholders})",
        values,
    )
    return len(rows)


def _write_load_metadata(connection, manifest):
    quoted_table = _sqlite_identifier(LOAD_MANIFEST_TABLE)
    connection.execute(f"DROP TABLE IF EXISTS {quoted_table}")
    connection.execute(
        f"""
        CREATE TABLE {quoted_table} (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    connection.executemany(
        f"INSERT INTO {quoted_table} (key, value) VALUES (?, ?)",
        ((key, json.dumps(value, sort_keys=True)) for key, value in manifest.items()),
    )


def load_onestepgps_reports(input_dir=None, output_db=None):
    input_path = Path(input_dir) if input_dir else DEFAULT_INPUT_DIR
    if not input_path.is_dir():
        raise NotADirectoryError(f"OneStepGPS input path must be a directory: {input_path}")

    db_path = Path(output_db) if output_db else DEFAULT_OUTPUT_DB
    db_path.parent.mkdir(parents=True, exist_ok=True)

    reports = _csv_reports(input_path)
    table_columns = None
    all_rows = []
    per_file_counts = {}

    for report in reports:
        columns, rows = _read_report_rows(report)
        if table_columns is None:
            table_columns = columns
        elif columns != table_columns:
            raise ValueError(f"{report} columns do not match earlier OneStepGPS reports")

        per_file_counts[report.name] = len(rows)
        all_rows.extend(rows)

    with sqlite3.connect(db_path) as connection:
        _create_table(connection, table_columns)
        row_count = _insert_rows(connection, table_columns, all_rows)

        manifest = {
            "loaded_at": datetime.now(timezone.utc).isoformat(),
            "source_dir": str(input_path),
            "source_files": [report.name for report in reports],
            "database_file": str(db_path),
            "timezone_transform": "EDT (UTC-04:00) to UTC",
            "row_counts": {TABLE_NAME: row_count},
            "per_file_counts": per_file_counts,
            "tables": [TABLE_NAME],
        }
        _write_load_metadata(connection, manifest)

    return manifest


def main():
    parser = argparse.ArgumentParser(
        description="Load OneStepGPS drives and stops CSV reports into SQLite."
    )
    parser.add_argument(
        "input_dir",
        nargs="?",
        type=Path,
        help="Directory of OneStepGPS CSV reports. Defaults to data/onestepgps_reports.",
    )
    parser.add_argument(
        "--output-db",
        type=Path,
        help="SQLite database file. Defaults to data/flh_insure_audit.db.",
    )
    args = parser.parse_args()

    manifest = load_onestepgps_reports(args.input_dir, args.output_db)
    print(f"Saved SQLite database to {manifest['database_file']}")
    print(f"{TABLE_NAME}: {manifest['row_counts'][TABLE_NAME]}")


if __name__ == "__main__":
    main()
