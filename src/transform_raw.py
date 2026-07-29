import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIR = PROJECT_ROOT / "data" / "argyle_reports"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "argyle_csv"


def _latest_json_file(input_dir=DEFAULT_INPUT_DIR):
    files = sorted(Path(input_dir).glob("*.json"), key=lambda path: path.stat().st_mtime)
    if not files:
        raise FileNotFoundError(f"No JSON files found in {input_dir}")
    return files[-1]


def _load_argyle_dump(input_file):
    with Path(input_file).open(encoding="utf-8") as raw_file:
        dump = json.load(raw_file)

    data = dump.get("data", dump)
    if isinstance(data, list) and isinstance(dump, dict):
        resource = dump.get("resource")
        if resource and resource != "all":
            data = {resource: data}

    if not isinstance(data, dict):
        raise ValueError("Argyle dump must contain a top-level object or a 'data' object.")

    return dump, data


def _json_cell(value):
    if value in ({}, []):
        return ""
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _flatten_record(record, prefix="", skip_paths=None):
    skip_paths = skip_paths or set()
    flattened = {}

    for key, value in record.items():
        path = f"{prefix}.{key}" if prefix else key
        column = path.replace(".", "_")

        if path in skip_paths:
            continue

        if isinstance(value, dict):
            flattened.update(_flatten_record(value, prefix=path, skip_paths=skip_paths))
        elif isinstance(value, list):
            flattened[column] = _json_cell(value)
        else:
            flattened[column] = value

    return flattened


def _append_list_children(rows, parent, list_field, parent_id_name, parent_id_value):
    values = parent.get(list_field) or []
    for position, value in enumerate(values, start=1):
        rows.append(
            {
                parent_id_name: parent_id_value,
                "field": list_field,
                "position": position,
                "value": value,
            }
        )


def _append_break_rows(rows, parent, parent_name):
    breaks = parent.get("all_datetimes", {}).get("breaks") or []
    for position, shift_break in enumerate(breaks, start=1):
        rows.append(
            {
                f"{parent_name}_id": parent.get("id"),
                "account": parent.get("account"),
                "employer": parent.get("employer"),
                "position": position,
                **_flatten_record(shift_break),
            }
        )


def _append_task_detail_rows(rows, parent, list_field):
    values = parent.get("task_details", {}).get(list_field) or []
    for position, value in enumerate(values, start=1):
        rows.append(
            {
                "gig_id": parent.get("id"),
                "account": parent.get("account"),
                "employer": parent.get("employer"),
                "position": position,
                **_flatten_record(value),
            }
        )


def _fieldnames(rows):
    seen = []
    for row in rows:
        for key in row:
            if key not in seen:
                seen.append(key)
    return seen


def _write_csv(output_dir, filename, rows, fieldnames=None):
    output_path = output_dir / filename
    output_path.parent.mkdir(parents=True, exist_ok=True)

    columns = fieldnames or _fieldnames(rows)
    with output_path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    return output_path


def transform_argyle_dump(input_file=None, output_dir=DEFAULT_OUTPUT_DIR):
    input_path = Path(input_file) if input_file else _latest_json_file()
    dump, data = _load_argyle_dump(input_path)

    output_path = Path(output_dir)
    if output_path == DEFAULT_OUTPUT_DIR:
        output_path = output_path / input_path.stem
    output_path.mkdir(parents=True, exist_ok=True)

    generated_files = []
    users = data.get("users", [])
    accounts = data.get("accounts", [])
    shifts = data.get("shifts", [])
    gigs = data.get("gigs", [])
    vehicles = data.get("vehicles", [])

    user_rows = [
        _flatten_record(
            user,
            skip_paths={"items_connected", "employers_connected"},
        )
        for user in users
    ]
    generated_files.append(_write_csv(output_path, "users.csv", user_rows))

    user_connection_rows = []
    for user in users:
        _append_list_children(user_connection_rows, user, "items_connected", "user_id", user.get("id"))
        _append_list_children(
            user_connection_rows,
            user,
            "employers_connected",
            "user_id",
            user.get("id"),
        )
    generated_files.append(
        _write_csv(
            output_path,
            "user_connections.csv",
            user_connection_rows,
            fieldnames=("user_id", "field", "position", "value"),
        )
    )

    account_rows = [_flatten_record(account) for account in accounts]
    generated_files.append(_write_csv(output_path, "accounts.csv", account_rows))

    shift_rows = [
        _flatten_record(shift, skip_paths={"all_datetimes.breaks"})
        for shift in shifts
    ]
    generated_files.append(_write_csv(output_path, "shifts.csv", shift_rows))

    shift_break_rows = []
    for shift in shifts:
        _append_break_rows(shift_break_rows, shift, "shift")
    generated_files.append(
        _write_csv(
            output_path,
            "shift_breaks.csv",
            shift_break_rows,
            fieldnames=("shift_id", "account", "employer", "position", "break_start", "break_end"),
        )
    )

    gig_rows = [
        _flatten_record(
            gig,
            skip_paths={
                "all_datetimes.breaks",
                "task_details.events",
                "task_details.orders",
            },
        )
        for gig in gigs
    ]
    generated_files.append(_write_csv(output_path, "gigs.csv", gig_rows))

    gig_break_rows = []
    gig_event_rows = []
    gig_order_rows = []
    for gig in gigs:
        _append_break_rows(gig_break_rows, gig, "gig")
        _append_task_detail_rows(gig_event_rows, gig, "events")
        _append_task_detail_rows(gig_order_rows, gig, "orders")
    generated_files.append(
        _write_csv(
            output_path,
            "gig_breaks.csv",
            gig_break_rows,
            fieldnames=("gig_id", "account", "employer", "position", "break_start", "break_end"),
        )
    )
    generated_files.append(
        _write_csv(
            output_path,
            "gig_events.csv",
            gig_event_rows,
            fieldnames=("gig_id", "account", "employer", "position"),
        )
    )
    generated_files.append(
        _write_csv(
            output_path,
            "gig_orders.csv",
            gig_order_rows,
            fieldnames=("gig_id", "account", "employer", "position"),
        )
    )

    vehicle_rows = [_flatten_record(vehicle) for vehicle in vehicles]
    generated_files.append(_write_csv(output_path, "vehicles.csv", vehicle_rows))

    manifest = {
        "transformed_at": datetime.now(timezone.utc).isoformat(),
        "source_file": str(input_path),
        "source_fetched_at": dump.get("fetched_at"),
        "row_counts": {
            "users": len(user_rows),
            "user_connections": len(user_connection_rows),
            "accounts": len(account_rows),
            "shifts": len(shift_rows),
            "shift_breaks": len(shift_break_rows),
            "gigs": len(gig_rows),
            "gig_breaks": len(gig_break_rows),
            "gig_events": len(gig_event_rows),
            "gig_orders": len(gig_order_rows),
            "vehicles": len(vehicle_rows),
        },
        "files": [str(path) for path in generated_files],
    }
    manifest_path = output_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    generated_files.append(manifest_path)

    return manifest


def main():
    parser = argparse.ArgumentParser(
        description="Transform raw Argyle report JSON into analysis-friendly CSVs."
    )
    parser.add_argument(
        "input_file",
        nargs="?",
        type=Path,
        help="Argyle JSON dump. Defaults to the newest file in data/argyle_reports.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for CSV output. Defaults to data/argyle_csv/<input-file-stem>.",
    )
    args = parser.parse_args()

    manifest = transform_argyle_dump(args.input_file, args.output_dir)
    print(f"Saved Argyle CSVs to {Path(manifest['files'][0]).parent}")
    for name, count in manifest["row_counts"].items():
        print(f"{name}: {count}")


if __name__ == "__main__":
    main()
