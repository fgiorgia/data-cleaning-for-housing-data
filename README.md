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

Create a `.env` in the root of your project and set your Postgres password as
the `DB_PASSWORD`. Alternatively, set it directly as an env variable.

The connection can be customised further; every variable except the password
has a default:

| Variable      | Default     | Purpose                          |
| ------------- | ----------- | -------------------------------- |
| `DB_PASSWORD` | *(required)*| Postgres password                |
| `DB_HOSTNAME` | `localhost` | Postgres host                    |
| `DB_PORT`     | `5432`      | Postgres port                    |
| `DB_USERNAME` | `postgres`  | Postgres user                    |
| `DB_DATABASE` | `postgres`  | Database the pipeline runs in    |

## Running the from-scratch cleaning pipeline

This loads `data/dataset.csv` into Postgres, cleans it with the SQL in
[`src/cleaning.sql`](src/cleaning.sql), and exports the result to
**`out/dataset.csv`**:

```sh
uv run poe data-cleaning-pipeline
```

The working tables (`"HousingDataRaw"` and `housing_data`) are dropped once
the export succeeds; the cleaned CSV is the pipeline's deliverable.

### Pipeline behaviour

- **Safe to re-run.** Loading replaces the raw table instead of appending,
  and the whole cleaning stage runs inside a single transaction: any failure
  rolls the database back to its pre-run state.
- **Fails fast on bad data.** `sale_price` and `sold_as_vacant` are validated
  before conversion; unparseable values abort the run with an explicit error
  instead of silently becoming `NULL`.
- **Removes exact duplicates.** Rows with the same parcel, address, price,
  sale date, and legal reference are collapsed to one (lowest `unique_id`
  kept).
- **Output types.** `sale_date` is a date, `sale_price` is numeric (decimals
  preserved), and `sold_as_vacant` is a boolean - it appears as `t`/`f` in
  the exported CSV, not `Y`/`N`.

## Provisioning the full enriched database from the backup

The from-scratch pipeline above produces the cleaned dataset only. The full
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

## Feature tools (restored `housing` database)

These tasks operate on the enriched database created by `restore-backup`
(they need `unique_addresses` / `address_mappings`, which only exist there).
Each defaults `DB_DATABASE` to `housing`; set it in your shell to override.

| Task | What it does |
| --- | --- |
| `uv run poe address-standardization` | (Re)applies `src/address_standardization.sql` and refreshes `address_standardized` — idempotent |
| `uv run poe geocoder --stats-only` | Geocoding CLI (OSM first, HERE fallback); `--stats-only` reports without spending API calls |
| `uv run poe show-map` | Renders `nashville_property_map.html` from geocoded addresses |
| `uv run poe data-quality-check` | Streamlit data-quality dashboard over `housing_data` |
| `uv run poe geocoding-dashboard` | Dash dashboard for reviewing/correcting geocodes |

The HERE fallback needs `HERE_API_KEY` in `.env`; without it the geocoder
still runs OSM-only.