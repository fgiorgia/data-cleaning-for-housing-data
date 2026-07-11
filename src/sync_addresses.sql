/**

Address sync bridge: housing_data -> the durable geocode cache.

Upserts distinct property/owner addresses from housing_data into
unique_addresses (never overwriting an existing cached geocode), then
rebuilds address_mappings from scratch so every housing_data row is linked
to its property and owner address_id.

Design notes:
- The CREATE TABLE IF NOT EXISTS block below is a fresh-clone bootstrap
  (DDL copied verbatim from src/schema.sql) and a no-op against an
  already-provisioned database.
- unique_addresses' real dedup key is its UNIQUE(address) constraint, NOT
  address_hash (address_hash only has a plain, non-unique index in
  schema.sql) -- ON CONFLICT (address) is what Postgres can actually
  enforce here.
- address_hash for newly inserted rows is computed as md5(address) on the
  as-stored (already uppercased) text, with NO case-folding. This matches
  every existing row empirically (verified against thousands of real rows);
  it does NOT match geocoding_service.py's _calculate_address_hash(), which
  lowercases first -- that function backs a code path
  (geocode_address(str)) that the real batch workflow never calls, and
  which is independently broken (its INSERT omits the NOT NULL `address`
  column). See .agents/tasks/TODO.md for that follow-up.
- Addresses are upserted as UPPER(TRIM(...)) so future casing drift from
  housing_data can't silently create a second cache entry for the same
  physical address.
- cleaning.sql drops the address_mappings_housing_id_fkey constraint via
  DROP TABLE ... CASCADE (housing_data must be freely rebuildable). This
  script re-adds both address_mappings FKs every run so referential
  integrity never stays broken for more than one pipeline step.

**/

\set ON_ERROR_STOP true

BEGIN;

----------------------------------------------- Bootstrap (no-op if present) -----------------------------------------------

CREATE EXTENSION IF NOT EXISTS postgis WITH SCHEMA public;

CREATE SEQUENCE IF NOT EXISTS public.unique_addresses_address_id_seq
    AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;

CREATE TABLE IF NOT EXISTS public.unique_addresses (
    address_id integer NOT NULL DEFAULT nextval('public.unique_addresses_address_id_seq'::regclass),
    address text NOT NULL,
    city text,
    state_validated boolean DEFAULT false,
    validation_notes text,
    latitude double precision,
    longitude double precision,
    corrected_address text,
    confidence double precision,
    source character varying(10),
    status character varying(20),
    geocoded_at timestamp without time zone,
    last_updated timestamp without time zone,
    address_hash character varying(32),
    geom public.geometry(Point,4326),
    address_standardized text,
    CONSTRAINT unique_addresses_pkey PRIMARY KEY (address_id),
    CONSTRAINT unique_addresses_address_key UNIQUE (address)
);

ALTER SEQUENCE public.unique_addresses_address_id_seq OWNED BY public.unique_addresses.address_id;

CREATE INDEX IF NOT EXISTS idx_address_hash ON public.unique_addresses USING btree (address_hash);
CREATE INDEX IF NOT EXISTS idx_unique_addresses_geom ON public.unique_addresses USING gist (geom);

CREATE SEQUENCE IF NOT EXISTS public.address_mappings_mapping_id_seq
    AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;

CREATE TABLE IF NOT EXISTS public.address_mappings (
    mapping_id integer NOT NULL DEFAULT nextval('public.address_mappings_mapping_id_seq'::regclass),
    housing_id integer NOT NULL,
    address_id integer NOT NULL,
    address_type text NOT NULL,
    CONSTRAINT address_mappings_pkey PRIMARY KEY (mapping_id)
);

ALTER SEQUENCE public.address_mappings_mapping_id_seq OWNED BY public.address_mappings.mapping_id;

CREATE INDEX IF NOT EXISTS idx_address_id ON public.address_mappings USING btree (address_id);
CREATE INDEX IF NOT EXISTS idx_housing_id ON public.address_mappings USING btree (housing_id);

CREATE SEQUENCE IF NOT EXISTS public.address_correction_log_id_seq
    AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;

CREATE TABLE IF NOT EXISTS public.address_correction_log (
    id integer NOT NULL DEFAULT nextval('public.address_correction_log_id_seq'::regclass),
    address_id integer NOT NULL,
    original_value text,
    new_value text,
    field_changed character varying(50),
    changed_by character varying(100),
    changed_at timestamp without time zone DEFAULT now(),
    reason text,
    CONSTRAINT address_correction_log_pkey PRIMARY KEY (id)
);

ALTER SEQUENCE public.address_correction_log_id_seq OWNED BY public.address_correction_log.id;

CREATE INDEX IF NOT EXISTS idx_correction_address_id ON public.address_correction_log USING btree (address_id);

DO $$ BEGIN
    ALTER TABLE public.address_correction_log
        ADD CONSTRAINT address_correction_log_address_id_fkey
        FOREIGN KEY (address_id) REFERENCES public.unique_addresses(address_id);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

----------------------------------------------- Upsert addresses into the cache -----------------------------------------------

-- housing_data has no separate city column: property_address/owner_address
-- store "STREET ADDRESS, CITY[, TN]" as one comma-separated string (verified
-- against real data -- schema.sql's property_city/owner_city columns don't
-- actually exist on this table). split_part(..., 1) is the street address
-- unique_addresses.address holds; split_part(..., 2) is the city.
WITH candidate_addresses AS (
    SELECT
        UPPER(TRIM(split_part(property_address, ',', 1))) AS address,
        NULLIF(UPPER(TRIM(split_part(property_address, ',', 2))), '') AS city
    FROM housing_data
    WHERE property_address IS NOT NULL AND TRIM(split_part(property_address, ',', 1)) <> ''
    UNION ALL
    SELECT
        UPPER(TRIM(split_part(owner_address, ',', 1))) AS address,
        NULLIF(UPPER(TRIM(split_part(owner_address, ',', 2))), '') AS city
    FROM housing_data
    WHERE owner_address IS NOT NULL AND TRIM(split_part(owner_address, ',', 1)) <> ''
),
-- One row per distinct address text; pick a single city deterministically
-- so re-runs over unchanged data always upsert the same values.
deduped_addresses AS (
    SELECT DISTINCT ON (address) address, city
    FROM candidate_addresses
    ORDER BY address, city NULLS LAST
)
INSERT INTO unique_addresses (address, city, address_hash)
SELECT address, city, md5(address)
FROM deduped_addresses
ON CONFLICT (address) DO NOTHING;

----------------------------------------------- Rebuild address_mappings from scratch -----------------------------------------------

TRUNCATE address_mappings;

INSERT INTO address_mappings (housing_id, address_id, address_type)
SELECT hd.unique_id, ua.address_id, 'property'
FROM housing_data hd
JOIN unique_addresses ua ON ua.address = UPPER(TRIM(split_part(hd.property_address, ',', 1)))
WHERE hd.property_address IS NOT NULL AND TRIM(split_part(hd.property_address, ',', 1)) <> '';

INSERT INTO address_mappings (housing_id, address_id, address_type)
SELECT hd.unique_id, ua.address_id, 'owner'
FROM housing_data hd
JOIN unique_addresses ua ON ua.address = UPPER(TRIM(split_part(hd.owner_address, ',', 1)))
WHERE hd.owner_address IS NOT NULL AND TRIM(split_part(hd.owner_address, ',', 1)) <> '';

----------------------------------------------- Restore FKs cleaning.sql's CASCADE may have dropped -----------------------------------------------

DO $$ BEGIN
    ALTER TABLE public.address_mappings
        ADD CONSTRAINT address_mappings_address_id_fkey
        FOREIGN KEY (address_id) REFERENCES public.unique_addresses(address_id);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE public.address_mappings
        ADD CONSTRAINT address_mappings_housing_id_fkey
        FOREIGN KEY (housing_id) REFERENCES public.housing_data(unique_id);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

COMMIT;
