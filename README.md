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

### Start Postgres/PostGIS

Start the local PostGIS database:

```bash
docker compose up -d postgis
```

The loaders connect using `DATABASE_URL` when set, otherwise they use the `POSTGRES_*` environment variables from `docker-compose.yml` with local defaults.

### Load all reports into Postgres

Load the newest transformed Argyle CSV directory and all OneStepGPS CSV reports:

```bash
python3 src/load_reports.py
```

Or pass specific report directories:

```bash
python3 src/load_reports.py \
  --argyle-input-dir data/argyle_csv/argyle_reports_20260728T114030Z \
  --onestepgps-input-dir data/onestepgps_reports
```

Datetime columns are loaded as `timestamptz`. `Lat/Lng` columns are loaded as `geometry(Point, 4326)`, and separate Argyle latitude/longitude pairs also get derived `*_geom` PostGIS point columns.

### Load transformed Argyle CSVs

```bash
python3 src/load_argyle_postgres.py
```

Or pass a specific transformed CSV directory:

```bash
python3 src/load_argyle_postgres.py data/argyle_csv/argyle_reports_20260728T114030Z
```

Each CSV is loaded into a table matching its filename, and load metadata is stored in `argyle_load_manifest`.

### Load OneStepGPS reports

Load OneStepGPS CSV reports into Postgres/PostGIS:

```bash
python3 src/load_onestepgps_postgres.py
```

Or pass a specific report directory:

```bash
python3 src/load_onestepgps_postgres.py data/onestepgps_reports
```

Each report is loaded into a table named after its report prefix, such as `drives_and_stops`, `drive_detail_breakdown`, `device_point`, and `day_start_end_breakdown`. Load metadata is stored in `onestepgps_load_manifest`, and report timestamps are converted from Eastern Time to UTC.
