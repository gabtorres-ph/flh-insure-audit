import argparse

from load_argyle_postgres import load_transformed_csvs
from load_onestepgps_postgres import load_onestepgps_reports


def main():
    parser = argparse.ArgumentParser(
        description="Load all available report CSVs into Postgres/PostGIS."
    )
    parser.add_argument(
        "--argyle-input-dir",
        help="Directory of transformed Argyle CSVs. Defaults to the newest directory in data/argyle_csv.",
    )
    parser.add_argument(
        "--onestepgps-input-dir",
        help="Directory of OneStepGPS CSV reports. Defaults to data/onestepgps_reports.",
    )
    args = parser.parse_args()

    argyle_manifest = load_transformed_csvs(args.argyle_input_dir)
    onestepgps_manifest = load_onestepgps_reports(args.onestepgps_input_dir)

    print("Loaded all reports into Postgres/PostGIS")
    print("Argyle:")
    for name, count in argyle_manifest["row_counts"].items():
        print(f"  {name}: {count}")
    print("OneStepGPS:")
    for name, count in onestepgps_manifest["row_counts"].items():
        print(f"  {name}: {count}")


if __name__ == "__main__":
    main()
