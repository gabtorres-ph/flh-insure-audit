# flh-insure-audit
Repo for downloading and reconciling reports for Fast Lane Hustle Car Rentals

### TODOs
- Module for fetching Argyle reports
- Module for fetching OneStepGPS reports

### Transform Argyle dumps

Convert the newest raw Argyle JSON dump into separate CSVs:

```bash
python3 src/transform_raw.py
```

Or pass a specific dump:

```bash
python3 src/transform_raw.py data/argyle_reports/argyle_reports_20260728T103657Z.json
```

CSV output is written to `data/argyle_csv/<input-file-stem>/`. The Argyle dump includes users, accounts, shifts, gigs, and vehicles; the transform writes each report to its own CSV, including `accounts.csv`.

### Load transformed CSVs into SQLite

Load the newest transformed CSV directory into a persisted SQLite database:

```bash
python3 src/load_sqlite.py
```

Or pass a specific transformed CSV directory:

```bash
python3 src/load_sqlite.py data/argyle_csv/argyle_reports_20260728T114030Z
```

SQLite output is written to `data/flh_insure_audit.db` by default. Each CSV is loaded into a table matching its filename, and load metadata is stored in `argyle_load_manifest`.

### Load OneStepGPS reports into SQLite

Load OneStepGPS drives and stops CSV reports into a persisted SQLite database:

```bash
python3 src/load_onestepgps_sqlite.py
```

Or pass a specific report directory:

```bash
python3 src/load_onestepgps_sqlite.py data/onestepgps_reports
```

SQLite output is written to `data/flh_insure_audit.db` by default, the same database used by the Argyle loader. All CSV rows are loaded into `drives_and_stops`, load metadata is stored in `onestepgps_load_manifest`, and report timestamps are converted from EDT to UTC.
