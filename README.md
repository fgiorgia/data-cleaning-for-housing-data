# Data cleaning for housing data

Cleaning the *Nashville Housing Data* dataset.

## Initial setup

### Postgres

A PostgreSQL instance is expected to be available on your machine. You can
download it here: <https://www.postgresql.org/download/>.

Make sure the following extensions are available to the server (the scripts
create them as needed): `fuzzystrmatch` (Levenshtein string distance), and -
for the backup-based provisioning - `postgis` and `pgagent`. The `psql` and
`pg_restore` command-line tools must also be on your `PATH`.

### Python

This project uses [uv](https://docs.astral.sh/uv/) to manage the virtual
environment and dependencies. Install uv, then sync the project:

```sh
uv sync
```

All commands below run through uv, e.g. `uv run poe <task>`.

### Environment variables

Create a `.env` in the root of your project and set your Postgres
password as the `DB_PASSWORD`. Alternatively, set it directly as an env variable.

## Running the from-scratch cleaning pipeline

This rebuilds the base `housing_data` table from `data/dataset.csv` using the
SQL in [`src/cleaning.sql`](src/cleaning.sql):

```sh
uv run poe data-cleaning-pipeline
```

## Provisioning the full enriched database from the backup

The from-scratch pipeline above produces the cleaned base table only. The full
system - PostGIS geometry, geocoded `unique_addresses`, address-mapping and
data-quality tables, and the address-parsing function library - was built with
external geocoding (OSM/HERE) and is captured in the binary archive
[`data/migration_dump.backup`](data/migration_dump.backup). Its authoritative
schema is committed, readable, in [`src/schema.sql`](src/schema.sql).

Because the backup depends on external geocoding that cannot be reproduced in
pure SQL, restore it instead of re-deriving it. The restore loads into a
dedicated database (default `housing`) so it never collides with the
from-scratch pipeline's `postgres` database:

```sh
uv run poe restore-backup          # creates the database if missing
uv run poe restore-backup-fresh    # drops and rebuilds it
```
