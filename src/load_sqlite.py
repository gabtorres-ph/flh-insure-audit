import argparse
import csv
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIR = PROJECT_ROOT / "data" / "argyle_csv"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "argyle_sqlite"


def _latest_csv_dir(input_dir=DEFAULT_INPUT_DIR):
    dirs = sorted(
        (path for path in Path(input_dir).iterdir() if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
    )
    if not dirs:
        raise FileNotFoundError(f"No transformed CSV directories found in {input_dir}")
    return dirs[-1]


def _sqlite_identifier(name):
    if not name:
        raise ValueError(f"Unsafe SQLite identifier: {name}")
    escaped_name = name.replace('"', '""')
    return f'"{escaped_name}"'


def _table_name(csv_path):
    return csv_path.stem


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


def _load_csv(connection, csv_path):
    table_name = _table_name(csv_path)

    with csv_path.open(encoding="utf-8", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        if reader.fieldnames is None:
            _create_table(connection, table_name, ["_empty_csv"])
            return 0

        columns = [column.strip() for column in reader.fieldnames]
        if not columns or all(not column for column in columns):
            _create_table(connection, table_name, ["_empty_csv"])
            return 0
        if not columns or any(not column for column in columns):
            raise ValueError(f"{csv_path} contains blank column names")

        rows = list(reader)

    _create_table(connection, table_name, columns)
    return _insert_rows(connection, table_name, columns, rows)


def _write_load_metadata(connection, manifest):
    connection.execute("DROP TABLE IF EXISTS load_manifest")
    connection.execute(
        """
        CREATE TABLE load_manifest (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    connection.executemany(
        "INSERT INTO load_manifest (key, value) VALUES (?, ?)",
        ((key, json.dumps(value, sort_keys=True)) for key, value in manifest.items()),
    )


def load_transformed_csvs(input_dir=None, output_db=None):
    input_path = Path(input_dir) if input_dir else _latest_csv_dir()
    if not input_path.is_dir():
        raise NotADirectoryError(f"CSV input path must be a directory: {input_path}")

    db_path = Path(output_db) if output_db else DEFAULT_OUTPUT_DIR / f"{input_path.name}.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    csv_paths = sorted(path for path in input_path.glob("*.csv") if path.is_file())
    if not csv_paths:
        raise FileNotFoundError(f"No CSV files found in {input_path}")

    row_counts = {}
    with sqlite3.connect(db_path) as connection:
        for csv_path in csv_paths:
            row_counts[_table_name(csv_path)] = _load_csv(connection, csv_path)

        manifest = {
            "loaded_at": datetime.now(timezone.utc).isoformat(),
            "source_dir": str(input_path),
            "database_file": str(db_path),
            "row_counts": row_counts,
            "tables": sorted(row_counts),
        }
        _write_load_metadata(connection, manifest)

    return manifest


def main():
    parser = argparse.ArgumentParser(
        description="Load transformed Argyle CSVs into a persisted SQLite database."
    )
    parser.add_argument(
        "input_dir",
        nargs="?",
        type=Path,
        help="Directory of transformed CSVs. Defaults to the newest directory in data/argyle_csv.",
    )
    parser.add_argument(
        "--output-db",
        type=Path,
        help="SQLite database file. Defaults to data/argyle_sqlite/<csv-dir-name>.db.",
    )
    args = parser.parse_args()

    manifest = load_transformed_csvs(args.input_dir, args.output_db)
    print(f"Saved SQLite database to {manifest['database_file']}")
    for name, count in manifest["row_counts"].items():
        print(f"{name}: {count}")


if __name__ == "__main__":
    main()
