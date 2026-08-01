import argparse
from datetime import datetime, timezone
from pathlib import Path

from postgres_loader import connect, load_table, read_csv_rows, write_manifest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIR = PROJECT_ROOT / "data" / "argyle_csv"
LOAD_MANIFEST_TABLE = "argyle_load_manifest"


def _latest_csv_dir(input_dir=DEFAULT_INPUT_DIR):
    dirs = sorted(
        (path for path in Path(input_dir).iterdir() if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
    )
    if not dirs:
        raise FileNotFoundError(f"No transformed CSV directories found in {input_dir}")
    return dirs[-1]


def _table_name(csv_path):
    return csv_path.stem


def load_transformed_csvs(input_dir=None):
    input_path = Path(input_dir) if input_dir else _latest_csv_dir()
    if not input_path.is_dir():
        raise NotADirectoryError(f"CSV input path must be a directory: {input_path}")

    csv_paths = sorted(path for path in input_path.glob("*.csv") if path.is_file())
    if not csv_paths:
        raise FileNotFoundError(f"No CSV files found in {input_path}")

    row_counts = {}
    with connect() as connection:
        for csv_path in csv_paths:
            columns, rows = read_csv_rows(csv_path)
            row_counts[_table_name(csv_path)] = load_table(
                connection,
                _table_name(csv_path),
                columns,
                rows,
            )

        manifest = {
            "loaded_at": datetime.now(timezone.utc).isoformat(),
            "source_dir": str(input_path),
            "database": "postgres",
            "spatial_reference_id": 4326,
            "row_counts": row_counts,
            "tables": sorted(row_counts),
        }
        write_manifest(connection, LOAD_MANIFEST_TABLE, manifest)

    return manifest


def main():
    parser = argparse.ArgumentParser(
        description="Load transformed Argyle CSVs into Postgres/PostGIS."
    )
    parser.add_argument(
        "input_dir",
        nargs="?",
        type=Path,
        help="Directory of transformed CSVs. Defaults to the newest directory in data/argyle_csv.",
    )
    args = parser.parse_args()

    manifest = load_transformed_csvs(args.input_dir)
    print("Loaded Argyle CSVs into Postgres/PostGIS")
    for name, count in manifest["row_counts"].items():
        print(f"{name}: {count}")


if __name__ == "__main__":
    main()
