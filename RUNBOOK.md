# Runbook

Step-by-step instructions for a new user. The project has three workflows
that share the same machine setup but target different databases:

| Workflow | Command | Database | Tables after run |
| --- | --- | --- | --- |
| **Cleaning pipeline** | `uv run poe data-cleaning-pipeline` | `housing_clean` (auto-created) | Dropped — `out/dataset.csv` is the deliverable |
| **Backup restore** | `uv run poe restore-backup` | `geocoded_housing` (auto-created) | Persist — browse them in DBeaver / psql |
| **Feature tools** | `uv run poe geocoding-dashboard` (and others) | `geocoded_housing` | Read/write the restored tables (§3) |

Each workflow creates its own database if missing — nothing is created by
hand. The feature tools all operate on the `geocoded_housing` database
created by the backup restore, so run **§2 before §3**.

---

## 0. One-time machine setup

### 0.1 PostgreSQL

Install PostgreSQL (15+) and confirm the server is running and the CLI tools
are on your `PATH`:

```sh
psql --version
pg_restore --version
pg_isready                # should print "accepting connections"
```

If `pg_isready` says the server is not running:
- Linux: `sudo service postgresql start`
- macOS (Homebrew): `brew services start postgresql`
- Windows: start the PostgreSQL service from Services, or re-run the
  installer's Stack Builder.

#### Extensions

The **cleaning pipeline** needs only `fuzzystrmatch` (ships with
`postgresql-contrib` on Linux, included by default on macOS/Windows
installers).

The **backup restore** additionally needs `postgis` and `pgagent`. Install
them once:
- Linux: `sudo apt install postgresql-contrib postgis postgresql-17-pgagent`
  (adjust the version number to match yours).
- macOS: `brew install postgis`; pgagent is included.
- Windows: run Stack Builder from the Start menu, select your PostgreSQL
  version, and install the PostGIS and pgAgent add-ons.

### 0.2 Python

Install [uv](https://docs.astral.sh/uv/), then from the repo root:

```sh
uv sync --all-groups
```

`uv sync` installs everything in `uv.lock`, including the feature-tool
dependencies added during the merge (`requests`, `dash`,
`dash-bootstrap-components`, `streamlit`, `plotly`, `psycopg2-binary`). A
fresh clone needs no extra `uv add`.

### 0.3 Environment variables

Create `.env` in the repo root:

```env
DB_PASSWORD=your_postgres_password
```

If you never set a password for the `postgres` user, set one now:

```sh
# Linux / macOS
sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'your_postgres_password';"
```

(On Windows the installer asked for this password during setup.)

Optional overrides (the defaults work for a standard local install):

| Variable | Default | Purpose |
| --- | --- | --- |
| `DB_HOSTNAME` | `localhost` | Postgres host |
| `DB_PORT` | `5432` | Postgres port |
| `DB_USERNAME` | `postgres` | Postgres user |
| `DB_DATABASE` | `postgres` | Fallback database for scripts run outside poe (each poe task group pins its own) |

The feature tools (§3) use one additional **optional** variable,
`HERE_API_KEY`, documented in §3.1. `.env` therefore ends up holding two
secrets — `DB_PASSWORD` and `HERE_API_KEY` — so keep it out of version
control (it is already gitignored; verify with `git check-ignore -v .env`).

---

## 1. Cleaning pipeline

Loads `data/dataset.csv`, cleans it in PostgreSQL, and exports the result to
`out/dataset.csv`. Working tables are dropped on success.

### Does the database exist?

The pipeline runs in its own `housing_clean` database, and its first step
(`ensure-clean-db`) creates it if missing — **there is nothing to create by
hand; skip straight to running the pipeline.**

### Run

```sh
uv run poe data-cleaning-pipeline
```

What it does, in order:

1. Creates `out/` if missing.
2. Strips the BOM from `data/dataset.csv` → `data/dataset_no_bom.csv`.
3. Loads it into `"HousingDataRaw"` (replacing any previous version).
4. Installs `fuzzystrmatch` if missing.
5. Cleans the data inside a single transaction, exports `out/dataset.csv`,
   and drops the working tables.

**Success** looks like `COPY <n>` followed by `COMMIT` at the end of the
output, and a fresh `out/dataset.csv` on disk.

**Re-running** is always safe: loads replace, the transaction rolls back on
failure, and leftover tables from a previous failure are dropped
automatically.

### Troubleshooting

| Error | Cause | Fix |
| --- | --- | --- |
| `Error: Missing Postgres password` | No `DB_PASSWORD` in `.env`/environment | Create `.env` (step 0.3) |
| `connection refused` | Postgres server not running | Start the service (step 0.1) |
| `password authentication failed` | Wrong password | Reset it (step 0.3) |
| `FATAL: database "..." does not exist` | `DB_DATABASE` points to a database never created | Create it (see above) |
| `No such file or directory: './data/dataset.csv'` | Dataset not present | Place the Nashville Housing CSV at `data/dataset.csv` |
| `sale_price: N row(s) cannot be parsed` | Non-price value in source data | Fix those rows in the CSV; the database was not changed (the transaction rolled back) |
| `sold_as_vacant: N row(s) hold unexpected values` | Value other than Yes/No/Y/N | Same as above |
| `extension "fuzzystrmatch" is not available` | `postgresql-contrib` not installed | Install the contrib package (step 0.1) |

---

## 2. Backup restore

Restores `data/migration_dump.backup` into a **separate** `geocoded_housing`
database.
This is the full enriched system: PostGIS geometry, geocoded
`unique_addresses`, address-mapping and data-quality tables, and the
address-parsing function library. Unlike the cleaning pipeline, the tables
**persist** — this is what you browse in DBeaver and what the §3 feature
tools read and write.

### First run (database does not exist yet)

The restore script creates the `geocoded_housing` database for you:

```sh
uv run poe restore-backup
```

What it does:

1. Checks that `data/migration_dump.backup` exists.
2. Creates the `geocoded_housing` database (running `CREATE DATABASE`
   through the always-present `postgres` maintenance database).
3. Filters the `spatial_ref_sys` data out of the restore list (PostGIS
   repopulates that table on extension creation, so the dump's copy would
   cause duplicate-key conflicts).
4. Runs `pg_restore` into `geocoded_housing`.

**Success** prints `Restore complete -> database 'geocoded_housing'`.

### Re-run (database already exists)

A plain `restore-backup` will refuse if the database already exists, to
prevent accidental data loss:

```
Database 'geocoded_housing' already exists. Re-run with --recreate to drop and rebuild it.
```

To drop and rebuild:

```sh
uv run poe restore-backup-fresh
```

This runs `DROP DATABASE ... WITH (FORCE)` (disconnecting any active
sessions) and then performs a clean restore.

### What you get

After a successful restore, the `geocoded_housing` database contains:

| Table / View | Purpose |
| --- | --- |
| `housing_data` | Cleaned sale records (same as the pipeline's output, with a primary key on `unique_id`) |
| `unique_addresses` | Deduplicated addresses with PostGIS geometry and geocoding results |
| `address_mappings` | Links each `housing_data` row to its `unique_addresses` entry |
| `data_quality_issues` | Flagged data problems found during enrichment |
| `address_correction_log` | History of geocoding corrections |
| `api_usage` | Geocoding API call tracking |
| `geocoding_status` (view) | Summary of geocoding completeness |
| `address_components_view` (view) | Parsed address parts |

Connect DBeaver to **database `geocoded_housing`** (not `postgres`) to see
all of these under Schemas → public → Tables.

### Troubleshooting

| Error | Cause | Fix |
| --- | --- | --- |
| `Error: Missing Postgres password` | No `DB_PASSWORD` | Create `.env` (step 0.3) |
| `Error: backup file not found` | `data/migration_dump.backup` missing | Make sure the file is present (it ships with the repo) |
| `Error: refusing to target '...'` | `--dbname` matches `DB_DATABASE` or `postgres` | The restore deliberately refuses to overwrite your working or maintenance database; use the default `geocoded_housing` or pick another name with `--dbname` |
| `Database 'geocoded_housing' already exists` | Previous restore succeeded | Use `restore-backup-fresh` to rebuild, or connect to the existing database — it's already set up |
| `Could not find 'pg_restore'` | PostgreSQL bin directory not on `PATH` | Add it (e.g. `export PATH="/usr/lib/postgresql/17/bin:$PATH"` on Linux, or `C:\Program Files\PostgreSQL\17\bin` on Windows) |
| `extension "postgis" is not available` | PostGIS not installed | Install it (step 0.1, Extensions) |
| `duplicate key value violates unique constraint` on `spatial_ref_sys` | Stale `out/restore_toc.list` from a previous interrupted run | Delete `out/restore_toc.list` and re-run |

---

## 3. Feature tools (the `geocoded_housing` database)

These tasks operate on the restored `geocoded_housing` database. They all
read or write `unique_addresses` / `address_mappings` / `housing_data`,
which **only exist in `geocoded_housing`**, so each task pins `DB_DATABASE`
to `geocoded_housing` in `pyproject.toml`.

**Prerequisite:** run `uv run poe restore-backup` (§2) first. Without it these
tools connect to a database that has no `unique_addresses` table and abort.

### 3.1 Extra setup: the HERE API key (optional)

Geocoding tries **OpenStreetMap / Nominatim first** — a free, open-source
service that needs no key — and falls back to **HERE**, a commercial
geocoder, only for addresses OSM cannot resolve. HERE is entirely optional:
without a key the geocoder runs OSM-only and simply marks the addresses HERE
would have rescued as `FAILED`.

To enable the fallback, add your key to `.env`:

```env
HERE_API_KEY=your_here_rest_api_key
```

Create the key at <https://platform.here.com> under Access Manager → your app
→ Credentials → **API Keys (REST)**. A freshly created key can take a few
minutes to activate, and the older `app_id` / `app_code` credentials do **not**
work with this endpoint.

> **Privacy note.** Both geocoders receive the address strings you send them:
> running the geocoder transmits addresses from `unique_addresses` to
> `nominatim.openstreetmap.org` and, on fallback, to HERE. The Nashville
> dataset is public property data, but treat the pattern as the general rule —
> geocoding sends location data to a third party. Keep `.env` (which holds
> `DB_PASSWORD` and `HERE_API_KEY`) gitignored, and note that the dashboard in
> §3.3 binds to `127.0.0.1` specifically so its data and debugger stay off the
> local network.

### 3.2 The tasks

| Task | Local URL | What it does |
| --- | --- | --- |
| `uv run poe address-standardization` | — | (Re)applies `src/address_standardization.sql` and refreshes `address_standardized`. Idempotent (`CREATE OR REPLACE` / `ADD COLUMN IF NOT EXISTS`), safe to re-run. |
| `uv run poe address-imputation` | — | Adds `property_address_imputed` / `owner_address_imputed` flags to `housing_data`, reconstructing which addresses the original migration filled in (by comparing against `data/dataset.csv`), and re-applies the parcel-sibling fills. Idempotent, safe to re-run. |
| `uv run poe data-quality-maintenance` | — | Removes duplicate sale records (same parcel, address, price, date, legal reference — the migration never deduplicated) together with their `address_mappings`, and flags addresses with placeholder house number 0 in `data_quality_issues`. Idempotent, safe to re-run. |
| `uv run poe geocoder --stats-only` | — | Geocoding CLI. `--stats-only` reports API usage + DB completeness **without spending any API calls**. Drop the flag to actually geocode. |
| `uv run poe show-map` | opens `nashville_property_map.html` | Renders a clustered Folium map of geocoded properties. The HTML is a generated artifact (gitignored); regenerate any time. |
| `uv run poe data-quality-check` | <http://localhost:8501> | Streamlit dashboard over `housing_data` (data-quality issues). |
| `uv run poe geocoding-dashboard` | <http://localhost:8050> | Dash dashboard for reviewing and correcting geocodes (§3.3). |

Stop a dashboard with `Ctrl+C` in its terminal.

### 3.3 Geocoding dashboard (Dash, port 8050)

```sh
uv run poe geocoding-dashboard
# then open http://localhost:8050 (hard-refresh with Ctrl+F5 if you had an old tab open)
```

**The work queue.** The status dropdown defaults to **"Needs attention"** —
every address that is either `FAILED` or has no coordinates yet. This is the
list you actually work through. Two things to know:

- **"Pending"** is the subset that has never been attempted (no coordinates,
  not explicitly failed). Never-geocoded rows have `status IS NULL`, so a
  plain status filter can't surface them — that's why the filter keys off
  coordinates, not just status.
- The **map only shows rows with coordinates**. Pending rows appear in the
  table but not on the map (they have nowhere to plot), which is expected.

**Refresh behaviour.** The page checks for changes every 30 seconds via a
cheap change token; it only re-reads the table and redraws the map when the
data actually changed. On an idle database it does almost no work per tick.
Saving a correction through the edit modal refreshes every panel immediately.

**Debug and network.** The in-browser debugger is opt-in — set `DASH_DEBUG=1`
before launching if you need tracebacks in the page. The auto-reloader is off
by design (file churn in the project tree — logs, `__pycache__`, editor/sync
tools — makes it restart-loop, which the browser reports as "Server
Unavailable"). Because of that, **stop the task before swapping
`src/geocoding_dashboard.py`, then relaunch** — don't edit it in place while
it's running. The server binds `127.0.0.1`, not `0.0.0.0`, so it is not
reachable from other machines.

**Optional indexes.** As the pending set shrinks, a partial index makes the
queue query an index scan instead of a sequential scan. Run once against
`geocoded_housing`:

```sh
# Linux/macOS: DB_DATABASE=geocoded_housing uv run python ./scripts/psql_with_config.py -c "..."
# Windows PowerShell:
$env:DB_DATABASE = "geocoded_housing"
uv run python ./scripts/psql_with_config.py -c "CREATE INDEX IF NOT EXISTS idx_ua_needs_attention ON unique_addresses (address_id) WHERE status = 'FAILED' OR latitude IS NULL OR longitude IS NULL; CREATE INDEX IF NOT EXISTS idx_ua_status ON unique_addresses (status);"
Remove-Item Env:\DB_DATABASE
```

### 3.4 Verifying which address was added or changed

Three layers, quickest to most authoritative:

1. **In the dashboard.** Every modal save writes to `address_correction_log`
   and refreshes the **Recent Corrections** table (date, user, address,
   field, from → to, reason). The edited row flips to
   `MANUALLY_CORRECTED` — pick that status in the filter to see only touched
   addresses (blue on the map).

2. **In the terminal.** `GeocodingService.manually_update_address` logs a
   line like `Manually updated address ID 4711 by <name>` on each save,
   alongside the normal `POST /_dash-update-component ... 200` access lines.

3. **In the database (the audit trail of record).** Run against
   `geocoded_housing`:

   ```sql
   SELECT l.changed_at, l.changed_by, ua.address, l.field_changed,
          l.original_value, l.new_value, l.reason
   FROM address_correction_log l
   JOIN unique_addresses ua ON ua.address_id = l.address_id
   ORDER BY l.changed_at DESC
   LIMIT 10;
   ```

   For newly *geocoded* addresses (from the CLI or the single-address box)
   rather than manual edits, sort by the geocode timestamp instead:

   ```sql
   SELECT address_id, address, latitude, longitude, source, status, geocoded_at
   FROM unique_addresses
   WHERE geocoded_at IS NOT NULL
   ORDER BY geocoded_at DESC
   LIMIT 10;
   ```

   Run these in DBeaver connected to `geocoded_housing`, or from the shell
   with `DB_DATABASE=geocoded_housing` set (as in the index example above).
   One modal save
   can produce two log rows — one for `corrected_address`, one for
   `coordinates` — which is correct, not duplication.

### 3.5 Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `HERE geocoding failed with status 401: apiKey invalid. apiKey not found` | The `HERE_API_KEY` value sent is stale, mistyped, or shadowed by a shell variable | Verify the key in isolation (below); check `.env` has `HERE_API_KEY=...` with no quotes/trailing space; check `$env:HERE_API_KEY` isn't set in your shell (`load_dotenv` won't override it); regenerate the key in the HERE portal if it's dead. The geocoder keeps running OSM-only meanwhile. |
| `HERE API quota reached for the current 24h window` | Local counter hit its 950-per-window safety margin | Wait for the window to expire — the quota re-enables 24 hours after the first HERE call that opened it (the exact time is in the warning and in `--stats-only`) — or geocode OSM-only meanwhile |
| Browser shows "Server Unavailable / the server did not respond" | The Dash process isn't running, was swapped while running, or the console is frozen by Windows Quick Edit (text selected in the terminal) | Confirm the task is running; press Enter/Esc in its console; relaunch and hard-refresh (Ctrl+F5) |
| `Address already in use` on launch | A previous dashboard process still owns port 8050 (or 8501 for Streamlit) | Kill the stale process — e.g. PowerShell: `Get-Process -Id (Get-NetTCPConnection -LocalPort 8050).OwningProcess \| Stop-Process` |
| Map is blank but the table has rows | Those rows aren't geocoded yet (no coordinates) | Expected under "Pending" / "Needs attention"; switch to "Geocoded" to see plotted points |
| `ModuleNotFoundError: dash` / `streamlit` / `requests` | Dependencies not synced (e.g. after pulling the merge) | `uv sync` |
| Required tables missing (`unique_addresses`, `address_mappings`, `housing_data`) | Running a feature tool before the backup restore, or against the wrong database | Run `uv run poe restore-backup` (§2); confirm `DB_DATABASE` is `geocoded_housing` |

**Verify a HERE key without the app in the way** (PowerShell):

```powershell
$key = ((Get-Content .env | Select-String '^HERE_API_KEY=').ToString() -split '=', 2)[1].Trim()
Invoke-RestMethod "https://geocode.search.hereapi.com/v1/geocode?q=Nashville,TN&apiKey=$key"
```

JSON with an `items` array means the key is good (so a 401 in the app points
to a shadowing shell variable). The same 401 here means the key itself is
invalid — regenerate it and update `.env`. No database cleanup is needed
afterward: addresses that 401'd were stored as `FAILED` with no coordinates,
so the geocoder and the dashboard's "Needs attention" filter pick them up
automatically on the next run.

---

## Connecting to the results in DBeaver

### Cleaning pipeline output

The cleaning pipeline drops its tables on success, so there is nothing to
browse in DBeaver — open `out/dataset.csv` directly. If you want the
table to persist for inspection, run `uv run poe data-cleaning-pipeline-keep`
instead (see the README for setup).

### Backup restore output

1. DBeaver → New Database Connection → PostgreSQL.
2. Host: `localhost`, Port: `5432`, **Database: `geocoded_housing`**, User:
   `postgres`, Password: your `DB_PASSWORD`.
3. Test Connection → Finish.
4. Expand: geocoded_housing → Schemas → public → Tables.

If you already have a connection open to `postgres` and wonder why the tables
aren't there: the restore targets `geocoded_housing`, a separate database.
Either create a new connection pointing to `geocoded_housing`, or tick
**Show all databases** in your existing connection's PostgreSQL tab
(right-click connection → Edit Connection → PostgreSQL) and then expand the
`geocoded_housing` node.

After any pipeline or restore run, press **F5** on the connection to refresh
DBeaver's metadata cache.
