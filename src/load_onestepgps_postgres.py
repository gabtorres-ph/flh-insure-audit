import argparse
import re
from datetime import datetime, timezone
from pathlib import Path

from postgres_loader import connect, load_table, read_csv_rows, write_manifest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIR = PROJECT_ROOT / "data" / "onestepgps_reports"
LOAD_MANIFEST_TABLE = "onestepgps_load_manifest"
TIMESTAMP_COLUMNS_BY_TABLE = {
    "day_start_end_breakdown": {"start_time", "end_time"},
    "device_point": {"date_time"},
    "drive_detail_breakdown": {"time"},
    "drives_and_stops": {"start_time", "end_time"},
}


def _table_name(csv_path):
    stem = Path(csv_path).stem
    table_name = re.sub(r"_\d{2}_\d{2}_\d{4}.*$", "", stem)
    if table_name == stem:
        table_name = re.sub(r"[^a-zA-Z0-9]+", "_", stem).strip("_").lower()
    return table_name


def _csv_reports(input_dir):
    reports = sorted(Path(input_dir).glob("*.csv"))
    if not reports:
        raise FileNotFoundError(f"No OneStepGPS CSV reports found in {input_dir}")
    return reports


def load_onestepgps_reports(input_dir=None):
    input_path = Path(input_dir) if input_dir else DEFAULT_INPUT_DIR
    if not input_path.is_dir():
        raise NotADirectoryError(f"OneStepGPS input path must be a directory: {input_path}")

    reports = _csv_reports(input_path)
    table_columns = {}
    table_rows = {}
    per_file_counts = {}

    for report in reports:
        table_name = _table_name(report)
        columns, rows = read_csv_rows(
            report,
            normalize_columns=True,
            timestamp_columns=TIMESTAMP_COLUMNS_BY_TABLE.get(table_name, set()),
        )

        if table_name not in table_columns:
            table_columns[table_name] = columns
            table_rows[table_name] = []
        elif columns != table_columns[table_name]:
            raise ValueError(f"{report} columns do not match earlier {table_name} reports")

        per_file_counts[report.name] = len(rows)
        table_rows[table_name].extend(rows)

    with connect() as connection:
        row_counts = {}
        for table_name in sorted(table_columns):
            row_counts[table_name] = load_table(
                connection,
                table_name,
                table_columns[table_name],
                table_rows[table_name],
            )

        manifest = {
            "loaded_at": datetime.now(timezone.utc).isoformat(),
            "source_dir": str(input_path),
            "source_files": [report.name for report in reports],
            "database": "postgres",
            "timezone_transform": "America/New_York to UTC",
            "spatial_reference_id": 4326,
            "row_counts": row_counts,
            "per_file_counts": per_file_counts,
            "tables": sorted(row_counts),
        }
        write_manifest(connection, LOAD_MANIFEST_TABLE, manifest)

    return manifest


def main():
    parser = argparse.ArgumentParser(
        description="Load OneStepGPS CSV reports into Postgres/PostGIS."
    )
    parser.add_argument(
        "input_dir",
        nargs="?",
        type=Path,
        help="Directory of OneStepGPS CSV reports. Defaults to data/onestepgps_reports.",
    )
    args = parser.parse_args()

    manifest = load_onestepgps_reports(args.input_dir)
    print("Loaded OneStepGPS CSV reports into Postgres/PostGIS")
    for name, count in manifest["row_counts"].items():
        print(f"{name}: {count}")


if __name__ == "__main__":
    main()
