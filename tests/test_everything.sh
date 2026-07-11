#!/usr/bin/env bash
# scripts/test_everything.sh — Nuke and rebuild the entire project.
#
# Usage:
#   chmod +x scripts/test_everything.sh
#   ./scripts/test_everything.sh                      # full run
#   ./scripts/test_everything.sh --skip-dashboards    # skip interactive tools
#
# Database layout (fully automated — no DB_DATABASE needed anywhere):
#   geocoded_housing_dev — cleaning/sync pipeline workspace (created by the
#                          ensure-geocoded-db step; refactor-geocode-lineage
#                          dev target, see pyproject.toml)
#   geocoded_housing     — enriched system (created by restore-backup)
#
# Prerequisites: PostgreSQL running, .env with DB_PASSWORD, extensions
# installed (fuzzystrmatch, postgis, pgagent). See RUNBOOK §0.

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No colour

SKIP_DASHBOARDS=false
for arg in "$@"; do
  case "$arg" in
    --skip-dashboards) SKIP_DASHBOARDS=true ;;
  esac
done

step=0
run_step() {
  step=$((step + 1))
  echo ""
  echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo -e "${CYAN}  Step ${step}: ${1}${NC}"
  echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

pass() { echo -e "  ${GREEN}✓ ${1}${NC}"; }
warn() { echo -e "  ${YELLOW}⚠ ${1}${NC}"; }
fail() { echo -e "  ${RED}✗ ${1}${NC}"; exit 1; }

# ──────────────────────────────────────────────────────────────────────────── #
#  PHASE 0 — ENVIRONMENT                                                      #
# ──────────────────────────────────────────────────────────────────────────── #

run_step "Load credentials"
# Source .env for DB_PASSWORD. DB_DATABASE is deliberately unset afterwards:
# each poe task carries its own default (pipeline: housing_clean, feature
# tools: housing), and a shell-level DB_DATABASE would override all of them —
# which is exactly the accident this prevents.
if [ -f .env ]; then
  set -a; source .env; set +a
fi

if [ -n "${DB_DATABASE:-}" ]; then
  warn "DB_DATABASE='${DB_DATABASE}' found in the environment — unsetting it."
  warn "Per-task defaults handle database targeting; remove it from .env too."
  unset DB_DATABASE
fi

if [ -z "${DB_PASSWORD:-}" ]; then
  fail "DB_PASSWORD not set — create .env (see RUNBOOK §0.3)"
fi

export PGPASSWORD="$DB_PASSWORD"
PG_HOST="${DB_HOSTNAME:-localhost}"
PG_PORT="${DB_PORT:-5432}"
PG_USER="${DB_USERNAME:-postgres}"
MAINT_DB="postgres"   # server maintenance database, used only for admin ops
PG="psql -h $PG_HOST -p $PG_PORT -U $PG_USER"
pass "Credentials loaded (server: $PG_HOST:$PG_PORT)"

# ──────────────────────────────────────────────────────────────────────────── #
#  PHASE 1 — CLEAN                                                            #
# ──────────────────────────────────────────────────────────────────────────── #

run_step "Clean intermediate files"
rm -f  data/dataset_no_bom.csv
rm -f  nashville_property_map.html
rm -rf out/
rm -f  geocoding.log geocoding_cli.log
pass "Intermediate files removed"

run_step "Drop project databases"
$PG -d "$MAINT_DB" -c 'DROP DATABASE IF EXISTS housing_clean WITH (FORCE);'         2>/dev/null || true
pass "Dropped 'housing_clean' (pipeline DB — ensure-clean-db recreates it)"
$PG -d "$MAINT_DB" -c 'DROP DATABASE IF EXISTS geocoded_housing WITH (FORCE);'      2>/dev/null || true
pass "Dropped 'geocoded_housing' (enriched DB — restore-backup recreates it)"
$PG -d "$MAINT_DB" -c 'DROP DATABASE IF EXISTS geocoded_housing_sanitised_tmp WITH (FORCE);' 2>/dev/null || true
pass "Dropped temp sanitisation database (if any)"

# ──────────────────────────────────────────────────────────────────────────── #
#  PHASE 2 — INSTALL                                                          #
# ──────────────────────────────────────────────────────────────────────────── #

run_step "Sync Python environment (all groups)"
uv sync --all-groups
pass "Dependencies installed"

# ──────────────────────────────────────────────────────────────────────────── #
#  PHASE 3 — QUALITY GATES (no database needed)                               #
# ──────────────────────────────────────────────────────────────────────────── #

run_step "Format check (black)"
uv run black --check .
pass "Formatting clean"

run_step "Type-check (pyright)"
uv run pyright
pass "Type-check clean"

run_step "Unit tests (pytest, no database)"
# Export invariants need out/dataset.csv and out/dataset_public.csv, which
# Phase 1 just deleted; they run in Phase 4 via `pytest -m export` after
# export-dataset rebuilds them.
uv run pytest -q -m "not export"
pass "Unit tests passed"

# ──────────────────────────────────────────────────────────────────────────── #
#  PHASE 4 — CLEANING PIPELINE + GEOCODE SYNC + EXPORT                        #
# ──────────────────────────────────────────────────────────────────────────── #

run_step "Run the from-scratch cleaning pipeline and sync the geocode cache"
uv run poe geocode-prep
pass "Pipeline complete, unique_addresses/address_mappings synced"

run_step "Export dataset CSVs"
uv run poe export-dataset
pass "out/dataset.csv and out/dataset_public.csv written"

run_step "Export invariant tests (pytest -m export)"
uv run pytest -m export -q
pass "Export invariants passed"

# ──────────────────────────────────────────────────────────────────────────── #
#  PHASE 5 — BACKUP RESTORE (geocoded_housing database)                       #
# ──────────────────────────────────────────────────────────────────────────── #

run_step "Restore the enriched housing database from backup"
uv run poe restore-backup
pass "Backup restored into 'geocoded_housing'"

# Quick sanity: do the key tables exist?
TABLE_COUNT=$($PG -d geocoded_housing -tAc \
  "SELECT count(*) FROM information_schema.tables
   WHERE table_schema = 'public'
     AND table_name IN ('housing_data','unique_addresses','address_mappings');")
if [ "$TABLE_COUNT" -eq 3 ]; then
  pass "All 3 core tables present in 'geocoded_housing'"
else
  fail "Expected 3 core tables, found $TABLE_COUNT"
fi

# ──────────────────────────────────────────────────────────────────────────── #
#  PHASE 6 — FEATURE TOOLS SMOKE TEST (geocoded_housing database)             #
# ──────────────────────────────────────────────────────────────────────────── #

run_step "Address standardisation (idempotent SQL)"
uv run poe address-standardization
pass "address_standardization.sql applied"

run_step "Property map generation"
uv run poe show-map
if [ -f nashville_property_map.html ]; then
  pass "nashville_property_map.html generated ($(du -h nashville_property_map.html | cut -f1))"
else
  fail "Map file not created"
fi

if [ "$SKIP_DASHBOARDS" = false ]; then
  run_step "Streamlit data-quality dashboard (3-second smoke test)"
  echo "  Starting Streamlit, will kill after 3 s..."
  timeout 3 uv run poe data-quality-check &>/dev/null || true
  pass "Streamlit started without import errors"

  run_step "Dash geocoding dashboard (3-second smoke test)"
  echo "  Starting Dash, will kill after 3 s..."
  timeout 3 uv run poe geocoding-dashboard &>/dev/null || true
  pass "Dash started without import errors"
else
  warn "Skipping dashboard smoke tests (--skip-dashboards)"
fi

# ──────────────────────────────────────────────────────────────────────────── #
#  PHASE 7 — SANITISED DUMP EXPORT                                            #
# ──────────────────────────────────────────────────────────────────────────── #

run_step "Export sanitised dump (privacy redaction)"
uv run poe export-sanitised-dump
pass "Sanitised migration_dump.backup written"

# Verify it's a valid pg_dump archive.
pg_restore --list data/migration_dump.backup > /dev/null 2>&1 \
  && pass "Dump is a valid pg_restore archive" \
  || fail "Dump is not a valid archive"

# ──────────────────────────────────────────────────────────────────────────── #
#  DONE                                                                        #
# ──────────────────────────────────────────────────────────────────────────── #

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  All ${step} steps passed.${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "  Deliverables:"
echo "    out/dataset.csv               — full export (owner coordinates, LOCAL ONLY)"
echo "    out/dataset_public.csv        — shareable export (owner coordinates redacted)"
echo "    nashville_property_map.html   — interactive map"
echo "    data/migration_dump.backup    — sanitised backup"
echo ""
echo "  Databases:"
echo "    geocoded_housing_dev  — pipeline/sync workspace (refactor-geocode-lineage dev target)"
echo "    geocoded_housing      — enriched (PostGIS, geocoded addresses)"
echo ""
echo "  Next: open nashville_property_map.html in a browser,"
echo "        or run 'uv run poe geocoding-dashboard' / 'uv run poe data-quality-check'."
