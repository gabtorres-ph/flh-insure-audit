import csv
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
from psycopg import sql


EASTERN_TIME = ZoneInfo("America/New_York")

DATETIME_COLUMN_NAMES = {
    "break_start",
    "break_end",
    "date_time",
    "end_time",
    "scanned_at",
    "start_time",
    "time",
}
DATETIME_SUFFIXES = ("_at", "_datetime")
LAT_COLUMN_SUFFIX = "_lat"
LNG_COLUMN_SUFFIXES = ("_lng", "_lon", "_long", "_longitude")
LATLNG_COLUMN_NAMES = {"lat_lng", "latlong", "latitude_longitude"}


def database_url():
    if os.getenv("DATABASE_URL"):
        return os.environ["DATABASE_URL"]

    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    dbname = os.getenv("POSTGRES_DB", "flh_insure_audit")
    user = os.getenv("POSTGRES_USER", "flh_insure_audit")
    password = os.getenv("POSTGRES_PASSWORD", "flh_insure_audit")
    return f"postgresql://{user}:{password}@{host}:{port}/{dbname}"


def quote_identifier(name):
    if not name:
        raise ValueError(f"Unsafe Postgres identifier: {name}")
    return sql.Identifier(name)


def normalize_column_name(name):
    normalized = name.strip().lower()
    normalized = re.sub(r"\(([^)]+)\)", r"_\1", normalized)
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized)
    return normalized.strip("_")


def is_datetime_column(column):
    return column in DATETIME_COLUMN_NAMES or column.endswith(DATETIME_SUFFIXES)


def is_latlng_column(column):
    return column in LATLNG_COLUMN_NAMES


def _parse_iso_timestamp(value):
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    return datetime.fromisoformat(normalized)


def parse_timestamp(value, row=None):
    value = (value or "").strip()
    if not value or value.lower() == "no data":
        return None

    cleaned = re.sub(r"\s+(EDT|EST)$", "", value, flags=re.IGNORECASE)
    formats = (
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%m/%d/%Y %I:%M:%S %p",
        "%m/%d/%Y %I:%M %p",
    )

    for fmt in formats:
        try:
            parsed = datetime.strptime(cleaned, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=EASTERN_TIME)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass

    if re.match(r"^\d{4}-\d{2}-\d{2}T", cleaned):
        parsed = _parse_iso_timestamp(cleaned)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    if row and row.get("date"):
        date_value = row["date"].strip()
        for time_fmt in ("%I:%M:%S %p", "%I:%M %p"):
            try:
                parsed_time = datetime.strptime(cleaned, time_fmt).time()
                parsed_date = datetime.strptime(date_value, "%m/%d/%Y").date()
                return datetime.combine(parsed_date, parsed_time, tzinfo=EASTERN_TIME).astimezone(
                    timezone.utc
                )
            except ValueError:
                pass

    raise ValueError(f"Cannot parse timestamp value: {value}")


def parse_float(value):
    value = (value or "").strip()
    if not value or value.lower() in {"no data", "n/a", "unknown"}:
        return None
    return float(value.replace("°", "").replace(",", ""))


def parse_latlng(value):
    value = (value or "").strip()
    if not value or value.lower() in {"no data", "n/a", "unknown"}:
        return None

    parts = [part.strip().replace("°", "") for part in value.split(",")]
    if len(parts) != 2:
        raise ValueError(f"Cannot parse lat/lng value: {value}")

    lat = float(parts[0])
    lng = float(parts[1])
    return point_ewkt(lng, lat)


def point_ewkt(lng, lat):
    return f"SRID=4326;POINT({lng} {lat})"


def find_coordinate_pairs(columns):
    column_set = set(columns)
    pairs = {}
    for column in columns:
        if not column.endswith(LAT_COLUMN_SUFFIX):
            continue

        prefix = column[: -len(LAT_COLUMN_SUFFIX)]
        for suffix in LNG_COLUMN_SUFFIXES:
            lng_column = f"{prefix}{suffix}"
            if lng_column in column_set:
                pairs[f"{prefix}_geom"] = (column, lng_column)
                break
    return pairs


def column_type(column):
    if is_datetime_column(column):
        return sql.SQL("TIMESTAMPTZ")
    if is_latlng_column(column):
        return sql.SQL("GEOMETRY(Point, 4326)")
    if column.endswith(LAT_COLUMN_SUFFIX) or column.endswith(LNG_COLUMN_SUFFIXES):
        return sql.SQL("DOUBLE PRECISION")
    return sql.SQL("TEXT")


def connect():
    return psycopg.connect(database_url())


def create_table(connection, table_name, columns, geometry_columns=None):
    geometry_columns = geometry_columns or []
    definitions = [
        sql.SQL("{} {}").format(quote_identifier(column), column_type(column))
        for column in columns
    ]
    definitions.extend(
        sql.SQL("{} GEOMETRY(Point, 4326)").format(quote_identifier(column))
        for column in geometry_columns
    )

    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE EXTENSION IF NOT EXISTS postgis"))
        cursor.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(quote_identifier(table_name)))
        cursor.execute(
            sql.SQL("CREATE TABLE {} ({})").format(
                quote_identifier(table_name),
                sql.SQL(", ").join(definitions),
            )
        )


def converted_value(column, row):
    value = row.get(column, "")
    if value is None or value == "":
        return None
    if is_datetime_column(column):
        return parse_timestamp(value, row)
    if is_latlng_column(column):
        return parse_latlng(value)
    if column.endswith(LAT_COLUMN_SUFFIX) or column.endswith(LNG_COLUMN_SUFFIXES):
        return parse_float(value)
    return value


def insert_rows(connection, table_name, columns, rows, coordinate_pairs=None):
    if not rows:
        return 0

    coordinate_pairs = coordinate_pairs or {}
    insert_columns = columns + list(coordinate_pairs)
    placeholders = []
    values = []

    for row in rows:
        row_values = [converted_value(column, row) for column in columns]
        for _, (lat_column, lng_column) in coordinate_pairs.items():
            lat = parse_float(row.get(lat_column))
            lng = parse_float(row.get(lng_column))
            row_values.append(point_ewkt(lng, lat) if lat is not None and lng is not None else None)
        values.append(row_values)

    for column in columns:
        if is_latlng_column(column):
            placeholders.append(sql.SQL("{}::geometry").format(sql.Placeholder()))
        else:
            placeholders.append(sql.Placeholder())

    for _ in coordinate_pairs:
        placeholders.append(sql.SQL("{}::geometry").format(sql.Placeholder()))

    with connection.cursor() as cursor:
        cursor.executemany(
            sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                quote_identifier(table_name),
                sql.SQL(", ").join(quote_identifier(column) for column in insert_columns),
                sql.SQL(", ").join(placeholders),
            ),
            values,
        )
    return len(rows)


def read_csv_rows(csv_path, normalize_columns=False, timestamp_columns=None):
    timestamp_columns = timestamp_columns or set()
    with Path(csv_path).open(encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        if reader.fieldnames is None:
            return ["_empty_csv"], []

        columns = [
            normalize_column_name(column) if normalize_columns else column.strip()
            for column in reader.fieldnames
        ]
        if not columns or all(not column for column in columns):
            return ["_empty_csv"], []
        if any(not column for column in columns):
            raise ValueError(f"{csv_path} contains blank column names")

        rows = []
        for row_number, raw_row in enumerate(reader, start=2):
            row = {
                columns[index]: (raw_row.get(original_column) or "").strip()
                for index, original_column in enumerate(reader.fieldnames)
            }
            row["source_file"] = Path(csv_path).name
            row["source_row_number"] = str(row_number)
            rows.append(row)

    for column in timestamp_columns:
        if column not in columns:
            continue
        if not is_datetime_column(column):
            DATETIME_COLUMN_NAMES.add(column)

    return columns + ["source_file", "source_row_number"], rows


def load_table(connection, table_name, columns, rows):
    coordinate_pairs = find_coordinate_pairs(columns)
    create_table(connection, table_name, columns, geometry_columns=coordinate_pairs.keys())
    return insert_rows(connection, table_name, columns, rows, coordinate_pairs=coordinate_pairs)


def write_manifest(connection, table_name, manifest):
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL("DROP TABLE IF EXISTS {}").format(quote_identifier(table_name))
        )
        cursor.execute(
            sql.SQL(
                """
                CREATE TABLE {} (
                    key TEXT PRIMARY KEY,
                    value JSONB
                )
                """
            ).format(quote_identifier(table_name))
        )
        cursor.executemany(
            sql.SQL("INSERT INTO {} (key, value) VALUES (%s, %s)").format(
                quote_identifier(table_name)
            ),
            ((key, json.dumps(value, sort_keys=True)) for key, value in manifest.items()),
        )
