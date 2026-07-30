import argparse
import csv
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIR = PROJECT_ROOT / "data" / "onestepgps_reports"
DEFAULT_OUTPUT_DB = PROJECT_ROOT / "data" / "flh_insure_audit.db"
LOAD_MANIFEST_TABLE = "onestepgps_load_manifest"
EASTERN_TIME = ZoneInfo("America/New_York")
REPORT_SPECS = (
    {
        "filename_prefix": "drives_and_stops",
        "table_name": "drives_and_stops",
        "timestamp_columns": {"start_time", "end_time"},
    },
    {
        "filename_prefix": "drive_detail_breakdown",
        "table_name": "drive_detail_breakdown",
        "timestamp_columns": {"time"},
    },
)


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


def _parse_eastern_timestamp(value):
    value = (value or "").strip()
    if not value or value.lower() == "no data":
        return ""

    parsed = datetime.strptime(value, "%m/%d/%Y %I:%M:%S %p")
    return (
        parsed.replace(tzinfo=EASTERN_TIME)
        .astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _report_spec(csv_path):
    filename = Path(csv_path).name
    for spec in REPORT_SPECS:
        if filename.startswith(spec["filename_prefix"]):
            return spec
    supported_prefixes = ", ".join(spec["filename_prefix"] for spec in REPORT_SPECS)
    raise ValueError(
        f"Unsupported OneStepGPS report {filename}. "
        f"Expected filename to start with one of: {supported_prefixes}"
    )


def _csv_reports(input_dir):
    reports = sorted(Path(input_dir).glob("*.csv"))
    if not reports:
        raise FileNotFoundError(f"No OneStepGPS CSV reports found in {input_dir}")
    return reports


def _read_report_rows(csv_path, timestamp_columns):
    with Path(csv_path).open(encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        if reader.fieldnames is None:
            return ["_empty_csv"], []

        columns = [_normalize_column_name(column) for column in reader.fieldnames]
        if any(not column for column in columns):
            raise ValueError(f"{csv_path} contains blank column names")

        rows = []
        for row_number, raw_row in enumerate(reader, start=2):
            row = {
                columns[index]: (raw_row.get(original_column) or "").strip()
                for index, original_column in enumerate(reader.fieldnames)
            }

            for column in timestamp_columns:
                if column in row:
                    row[column] = _parse_eastern_timestamp(row[column])

            row["source_file"] = Path(csv_path).name
            row["source_row_number"] = str(row_number)
            rows.append(row)

    return columns + ["source_file", "source_row_number"], rows


def _create_table(connection, table_name, columns):
    quoted_table = _sqlite_identifier(table_name)
    quoted_columns = ", ".join(f"{_sqlite_identifier(column)} TEXT" for column in columns)

    connection.execute(f"DROP TABLE IF EXISTS {quoted_table}")
    connection.execute(f"CREATE TABLE {quoted_table} ({quoted_columns})")


def _insert_rows(connection, table_name, columns, rows):
    if not rows:
        return 0

    quoted_table = _sqlite_identifier(table_name)
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
    table_columns = {}
    table_rows = {}
    per_file_counts = {}

    for report in reports:
        spec = _report_spec(report)
        table_name = spec["table_name"]
        columns, rows = _read_report_rows(report, spec["timestamp_columns"])

        if table_name not in table_columns:
            table_columns[table_name] = columns
            table_rows[table_name] = []
        elif columns != table_columns[table_name]:
            raise ValueError(f"{report} columns do not match earlier {table_name} reports")

        per_file_counts[report.name] = len(rows)
        table_rows[table_name].extend(rows)

    with sqlite3.connect(db_path) as connection:
        row_counts = {}
        for table_name in sorted(table_columns):
            _create_table(connection, table_name, table_columns[table_name])
            row_counts[table_name] = _insert_rows(
                connection,
                table_name,
                table_columns[table_name],
                table_rows[table_name],
            )

        manifest = {
            "loaded_at": datetime.now(timezone.utc).isoformat(),
            "source_dir": str(input_path),
            "source_files": [report.name for report in reports],
            "database_file": str(db_path),
            "timezone_transform": "America/New_York to UTC",
            "row_counts": row_counts,
            "per_file_counts": per_file_counts,
            "tables": sorted(row_counts),
        }
        _write_load_metadata(connection, manifest)

    return manifest


def main():
    parser = argparse.ArgumentParser(
        description="Load OneStepGPS CSV reports into SQLite."
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
    for name, count in manifest["row_counts"].items():
        print(f"{name}: {count}")


if __name__ == "__main__":
    main()
