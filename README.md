# Nashville Housing — SQL cleaning pipeline & geo-enrichment

[![CI](https://github.com/fgiorgia/data-cleaning-for-housing-data/actions/workflows/ci.yml/badge.svg)](https://github.com/fgiorgia/data-cleaning-for-housing-data/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

This project started from the classic _Nashville Housing Data_ cleaning
exercise (the dataset popularised by Alex The Analyst's SQL portfolio
tutorial) and extends it into a small, reproducible data system:

- a **transactional PostgreSQL cleaning pipeline** that fails fast on bad
  data instead of silently producing NULLs, exports a typed CSV, and is safe
  to re-run;
- a **hybrid geocoding service** (OpenStreetMap/Nominatim first, HERE as an
  optional commercial fallback) with API-usage tracking, rate limiting, and a
  correction audit log;
- **PostGIS enrichment** with deduplicated addresses, address parsing
  functions, and an interactive property map;
- two **data-quality dashboards** (Streamlit and Dash) for inspecting the
  data and reviewing/correcting geocodes;
- **CI that asserts data invariants** on the exported dataset, plus linting,
  type checking, and unit tests.

<!-- TODO: add 2–3 screenshots. Recruiters skim: a GIF of the map and a
     dashboard screenshot are worth more than any paragraph.
![Property map](docs/img/property_map.png)
![Geocoding dashboard](docs/img/geocoding_dashboard.png)
-->

## Architecture at a glance

```
data/dataset.csv ──▶ remove_bom ──▶ load_csv ──▶ cleaning.sql (one transaction)
                                                      │
                                                      ▼
                                          sync-addresses (upserts the
                                          durable geocode cache: PostGIS,
                                          unique_addresses, address_mappings)
                                                      │
                         ┌────────────────────────────┼──────────────────────┐
                         ▼                            ▼                      ▼
                  geocoder (OSM→HERE,         dashboards (Dash,        property map
                  manual, rate-limited)       Streamlit)               (folium)
                         │
                         ▼
                  export-dataset ──▶ out/dataset.csv        (full, LOCAL ONLY)
                                  └─▶ out/dataset_public.csv (owner coords redacted)
```

## Data provenance & licensing

- **Dataset:** _Nashville Housing Data_ — Davidson County, TN property sale
  records, distributed via Kaggle. Public-record data; see the Kaggle page
  for its terms. The raw CSV is included at `data/dataset.csv` for
  reproducibility.
- **Code:** MIT — see [LICENSE](LICENSE).
- **Geocoding results:** coordinates in the enriched database derived from
  Nominatim are © [OpenStreetMap](https://www.openstreetmap.org/copyright)
  contributors and available under the
  [ODbL](https://opendatacommons.org/licenses/odbl/). HERE-derived results
  are subject to HERE's terms of service.

### Privacy note

Geocoding transmits address strings to third-party services
(`nominatim.openstreetmap.org`, and HERE on fallback). This dataset is
public property-sale data, but treat the pattern as the general rule:
geocoding sends location data to a third party. `out/dataset_public.csv`
(see below) is the **shareable export with owner mailing-address
coordinates redacted** — only property addresses carry coordinates in that
file. `out/dataset.csv` keeps both and stays local (`out/` is gitignored,
never distributed). If you need to geocode sensitive addresses, prefer a
self-hosted open-source geocoder such as [Nominatim](https://nominatim.org/),
[Photon](https://github.com/komoot/photon), or
[Pelias](https://pelias.io/) so the data never leaves your machine.

## Initial setup

### PostgreSQL

A PostgreSQL instance is expected on your machine
(<https://www.postgresql.org/download/>). The scripts create extensions as
needed; make sure these are _available_ to the server: `fuzzystrmatch`
(Levenshtein distance), and — for the geocode cache — `postgis` and
`pgagent`. The `psql` CLI tool must be on your `PATH`.

### Python

The project is managed with [uv](https://docs.astral.sh/uv/):

```sh
uv sync --all-groups
```

All commands below run through uv, e.g. `uv run poe <task>`.

### Environment variables

Create a `.env` in the project root with your Postgres password as
`DB_PASSWORD` (or export it directly). Everything else has a default:

| Variable      | Default      | Purpose                       |
| ------------- | ------------ | ----------------------------- |
| `DB_PASSWORD` | _(required)_ | Postgres password             |
| `DB_HOSTNAME` | `localhost`  | Postgres host                 |
| `DB_PORT`     | `5432`       | Postgres port                 |
| `DB_USERNAME` | `postgres`   | Postgres user                 |
| `DB_DATABASE` | `postgres`   | Database the pipeline runs in |

`.env` may also hold the optional `HERE_API_KEY` (see below). It is
gitignored; verify with `git check-ignore -v .env`.

## Running the from-scratch cleaning pipeline

Loads `data/dataset.csv` into Postgres and cleans it with
[`src/cleaning.sql`](src/cleaning.sql):

```sh
uv run poe data-cleaning-pipeline
```

The working tables (`"HousingDataRaw"`, `housing_data`) are dropped once
cleaning succeeds. This step alone produces no CSV — see "Building the
geocode cache and exporting" below for the full path to
`out/dataset.csv`/`out/dataset_public.csv`. Use `data-cleaning-pipeline-keep`
to keep `housing_data` around instead of dropping it (needed before syncing
addresses into the geocode cache, and useful for inspection).

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
- **Tracks imputation.** Addresses filled in from sibling parcel rows are
  flagged in `*_imputed` provenance columns rather than silently invented.
- **Output types.** `sale_date` is a date, `sale_price` is numeric (decimals
  preserved), `sold_as_vacant` is a boolean (`t`/`f` in the CSV).

## Building the geocode cache and exporting

The full system — PostGIS geometry, geocoded `unique_addresses`,
address-mapping and data-quality tables, and the address-parsing function
library — lives in a single database, `geocoded_housing`. Its authoritative,
readable schema is committed at [`src/schema.sql`](src/schema.sql). Unlike
the cleaning pipeline's disposable working tables, this geocode cache
**persists and is never dropped or truncated** by any automated step —
population is upsert-only, so re-running never re-geocodes or loses
existing corrections.

```sh
uv run poe geocode-prep      # cleans housing_data, then upserts addresses
                              # into unique_addresses/address_mappings
uv run poe geocoder          # optional: geocode with your own API keys
                              # (manual, rate-limited -- never run in CI)
uv run poe export-dataset    # writes out/dataset.csv and
                              # out/dataset_public.csv from whatever the
                              # cache currently holds
```

`export-dataset` works with a partially- or un-geocoded cache (a LEFT JOIN
leaves coordinates blank), so you can run it right after `geocode-prep`
without ever running the geocoder — or just use the published
`out/dataset_public.csv` if you don't need your own fresh geocodes.

## Feature tools (the `geocoded_housing` database)

Each task sets `DB_DATABASE` to `geocoded_housing` (the database
`geocode-prep` provisions).

| Task                                   | What it does                                                                                          |
| -------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| `uv run poe address-standardization`   | (Re)applies `src/address_standardization.sql` — idempotent                                            |
| `uv run poe address-imputation`        | Backfills `*_imputed` provenance flags and parcel-sibling address fills — idempotent                  |
| `uv run poe data-quality-maintenance`  | Removes duplicate sale records and flags placeholder addresses in `data_quality_issues` — idempotent  |
| `uv run poe geocoder --stats-only`     | Geocoding CLI (OSM first, HERE fallback); `--stats-only` reports without spending API calls           |
| `uv run poe show-map`                  | Renders `nashville_property_map.html` from geocoded addresses                                         |
| `uv run poe data-quality-check`        | Streamlit data-quality dashboard over `housing_data`                                                  |
| `uv run poe geocoding-dashboard`       | Dash dashboard for reviewing/correcting geocodes                                                      |

The HERE fallback needs `HERE_API_KEY` in `.env`; without it the geocoder
runs OSM-only. OSM calls respect Nominatim's usage policy (max 1 request per
second, identifying `User-Agent`).

## Quality gates

```sh
uv run poe lint        # ruff check + format check
uv run poe typecheck   # mypy
uv run poe test        # pytest (unit tests)
uv run poe check       # all of the above
```

CI runs the same gates on every push and pull request, then runs the full
pipeline against a real Postgres 17 service container and asserts invariants
on the exported CSV (non-empty, no NULL property addresses, boolean
`sold_as_vacant`, no double spaces). See
[`.github/workflows/ci.yml`](.github/workflows/ci.yml).

## Design decisions & trade-offs

- **SQL-first cleaning** keeps transformations declarative, set-based, and
  reviewable; Python handles orchestration and I/O only. A natural next step
  is porting the transformations to [dbt-core](https://github.com/dbt-labs/dbt-core)
  for built-in tests, docs, and lineage.
- **Hybrid geocoding** keeps costs at zero for the ~90% of addresses OSM
  resolves, spending the commercial quota only on the remainder — with a
  hard daily cap and a persistent usage counter.
- **A durable, upsert-only geocode cache instead of a shipped binary dump**:
  geocoding results are not reproducible offline, so they persist in
  `unique_addresses`/`address_mappings` and are rebuilt into CSV via
  `export-dataset` rather than distributed as a database backup. The schema
  is committed in plain SQL (`src/schema.sql`) for review; consumers who
  don't want to run their own geocoding just use the published
  `out/dataset_public.csv`.

See [RUNBOOK.md](RUNBOOK.md) for operations and troubleshooting, and
[CHANGELOG.md](CHANGELOG.md) for project history.
