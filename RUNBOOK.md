# Runbook

Step-by-step instructions for a new user. The project has two independent
workflows that share the same machine setup but target different databases:

| Workflow | Command | Database | Tables after run |
| --- | --- | --- | --- |
| **Cleaning pipeline** | `uv run poe data-cleaning-pipeline` | `postgres` (default) | Dropped — `out/dataset.csv` is the deliverable |
| **Backup restore** | `uv run poe restore-backup` | `housing` (dedicated) | Persist — browse them in DBeaver / psql |

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
uv sync
```

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
| `DB_DATABASE` | `postgres` | Database the **cleaning pipeline** runs in |

---

## 1. Cleaning pipeline

Loads `data/dataset.csv`, cleans it in PostgreSQL, and exports the result to
`out/dataset.csv`. Working tables are dropped on success.

### Does the database exist?

By default the pipeline runs in the `postgres` database, which exists in
every PostgreSQL installation, so **with default settings there is nothing
to create — skip straight to running the pipeline.**

If you want the pipeline in its own database (recommended once you also use
the backup restore, which occupies `housing`):

```sh
psql -h localhost -U postgres -c "CREATE DATABASE housing_clean;"
```

Then add to `.env`:

```env
DB_DATABASE=housing_clean
```

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

Restores `data/migration_dump.backup` into a **separate** `housing` database.
This is the full enriched system: PostGIS geometry, geocoded
`unique_addresses`, address-mapping and data-quality tables, and the
address-parsing function library. Unlike the cleaning pipeline, the tables
**persist** — this is what you browse in DBeaver.

### First run (database does not exist yet)

The restore script creates the `housing` database for you:

```sh
uv run poe restore-backup
```

What it does:

1. Checks that `data/migration_dump.backup` exists.
2. Creates the `housing` database (using your configured Postgres
   connection to run `CREATE DATABASE`).
3. Filters the `spatial_ref_sys` data out of the restore list (PostGIS
   repopulates that table on extension creation, so the dump's copy would
   cause duplicate-key conflicts).
4. Runs `pg_restore` into `housing`.

**Success** prints `Restore complete -> database 'housing'`.

### Re-run (database already exists)

A plain `restore-backup` will refuse if the database already exists, to
prevent accidental data loss:

```
Database 'housing' already exists. Re-run with --recreate to drop and rebuild it.
```

To drop and rebuild:

```sh
uv run poe restore-backup-fresh
```

This runs `DROP DATABASE ... WITH (FORCE)` (disconnecting any active
sessions) and then performs a clean restore.

### What you get

After a successful restore, the `housing` database contains:

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

Connect DBeaver to **database `housing`** (not `postgres`) to see all of
these under Schemas → public → Tables.

### Troubleshooting

| Error | Cause | Fix |
| --- | --- | --- |
| `Error: Missing Postgres password` | No `DB_PASSWORD` | Create `.env` (step 0.3) |
| `Error: backup file not found` | `data/migration_dump.backup` missing | Make sure the file is present (it ships with the repo) |
| `Error: refusing to target the configured database 'postgres'` | `--dbname` matches `DB_DATABASE` | The restore deliberately refuses to overwrite your working database; use the default `housing` or pick another name with `--dbname` |
| `Database 'housing' already exists` | Previous restore succeeded | Use `restore-backup-fresh` to rebuild, or connect to the existing database — it's already set up |
| `Could not find 'pg_restore'` | PostgreSQL bin directory not on `PATH` | Add it (e.g. `export PATH="/usr/lib/postgresql/17/bin:$PATH"` on Linux, or `C:\Program Files\PostgreSQL\17\bin` on Windows) |
| `extension "postgis" is not available` | PostGIS not installed | Install it (step 0.1, Extensions) |
| `duplicate key value violates unique constraint` on `spatial_ref_sys` | Stale `out/restore_toc.list` from a previous interrupted run | Delete `out/restore_toc.list` and re-run |

---

## Connecting to the results in DBeaver

### Cleaning pipeline output

The cleaning pipeline drops its tables on success, so there is nothing to
browse in DBeaver — open `out/dataset.csv` directly. If you want the
table to persist for inspection, run `uv run poe data-cleaning-pipeline-keep`
instead (see the README for setup).

### Backup restore output

1. DBeaver → New Database Connection → PostgreSQL.
2. Host: `localhost`, Port: `5432`, **Database: `housing`**, User:
   `postgres`, Password: your `DB_PASSWORD`.
3. Test Connection → Finish.
4. Expand: housing → Schemas → public → Tables.

If you already have a connection open to `postgres` and wonder why the tables
aren't there: the restore targets `housing`, a separate database. Either
create a new connection pointing to `housing`, or tick **Show all databases**
in your existing connection's PostgreSQL tab (right-click connection → Edit
Connection → PostgreSQL) and then expand the `housing` node.

After any pipeline or restore run, press **F5** on the connection to refresh
DBeaver's metadata cache.