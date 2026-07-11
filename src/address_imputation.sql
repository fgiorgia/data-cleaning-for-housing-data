/**

Imputation provenance for the geocoded_housing database.
Run by `uv run poe address-imputation` against the database provisioned by
`geocode-prep`.

The original migration filled missing addresses (impute_address_with_criteria
plus parcel-sibling fills) without recording which rows it touched. The source
CSV still knows: any row whose address is empty in data/dataset.csv but
populated here was derived, not sourced. This script reconstructs that
provenance into the same flag columns the cleaning pipeline writes
(src/cleaning.sql), then re-applies the parcel-sibling fills so the flags stay
accurate for rows that are still missing an address.

Idempotent: columns are added with IF NOT EXISTS, the provenance backfill is
deterministic, and the sibling fills only touch rows that are still NULL.

Limitation: any row absent from data/dataset.csv keeps its flags at false -
its provenance is unknown. (With the current lineage housing_data is rebuilt
from that CSV, so in practice every row is present.)

**/

\set ON_ERROR_STOP true

SET client_encoding TO 'UTF8';

BEGIN;

ALTER TABLE housing_data ADD COLUMN IF NOT EXISTS property_address_imputed boolean NOT NULL DEFAULT false;
ALTER TABLE housing_data ADD COLUMN IF NOT EXISTS owner_address_imputed    boolean NOT NULL DEFAULT false;

-- Snapshot of the raw CSV, used only to answer "was this value in the
-- source?". CSV HEADER skips the first line wholesale, so the BOM in
-- data/dataset.csv never reaches the parser and the un-BOMed copy produced
-- by remove-bom is not required here.
CREATE TEMP TABLE source_snapshot (
	unique_id        integer,
	parcel_id        text,
	land_use         text,
	property_address text,
	sale_date        text,
	sale_price       text,
	legal_reference  text,
	sold_as_vacant   text,
	owner_name       text,
	owner_address    text,
	acreage          text,
	tax_district     text,
	land_value       text,
	building_value   text,
	total_value      text,
	year_built       text,
	bedrooms         text,
	full_bath        text,
	half_bath        text
) ON COMMIT DROP;

\copy source_snapshot FROM './data/dataset.csv' DELIMITER ',' CSV HEADER

-- Historical fills: the source had no address but the database has one.
UPDATE housing_data hd
SET property_address_imputed = true
FROM source_snapshot s
WHERE s.unique_id = hd.unique_id
  AND (s.property_address IS NULL OR trim(s.property_address) = '')
  AND hd.property_address IS NOT NULL
  AND NOT hd.property_address_imputed;

UPDATE housing_data hd
SET owner_address_imputed = true
FROM source_snapshot s
WHERE s.unique_id = hd.unique_id
  AND (s.owner_address IS NULL OR trim(s.owner_address) = '')
  AND hd.owner_address IS NOT NULL
  AND NOT hd.owner_address_imputed;

-- Parcel-sibling fills, as in src/cleaning.sql. The address columns embed
-- the city ("STREET, CITY[, TN]"), so copying the sibling's address carries
-- a coherent city with it. cleaning.sql already performs these same fills
-- when it rebuilds the table; these re-apply them (and the flags) only for
-- rows that are somehow still NULL, so re-running stays a no-op normally.
UPDATE housing_data hd
SET property_address = hd2.property_address,
    property_address_imputed = true
FROM housing_data hd2
WHERE hd.parcel_id = hd2.parcel_id
  AND hd.unique_id <> hd2.unique_id
  AND hd.property_address IS NULL
  AND hd2.property_address IS NOT NULL;

UPDATE housing_data hd
SET owner_address = hd2.owner_address,
    owner_address_imputed = true
FROM housing_data hd2
WHERE hd.parcel_id = hd2.parcel_id
  AND hd.unique_id <> hd2.unique_id
  AND hd.owner_address IS NULL
  AND hd2.owner_address IS NOT NULL
  AND hd.owner_name = hd2.owner_name;

-- Summary for the operator.
SELECT count(*) FILTER (WHERE property_address_imputed) AS property_address_imputed,
       count(*) FILTER (WHERE owner_address_imputed)    AS owner_address_imputed,
       count(*) FILTER (WHERE property_address IS NULL) AS property_address_still_null,
       count(*) FILTER (WHERE owner_address IS NULL)    AS owner_address_still_null
FROM housing_data;

COMMIT;
