/**

Nashville Housing Data - from-scratch cleaning pipeline.
Run by `uv run poe data-cleaning-pipeline` against the table loaded by
scripts/load_csv.py.

Design notes:
- ON_ERROR_STOP is set on line 1 so *every* statement, including extension
  and function creation, is fail-fast.
- All data work runs inside a single transaction: a failure anywhere rolls
  the database back to its pre-run state, so the pipeline is safely
  re-runnable.
- Type conversions are validated before they run; unparseable values abort
  the pipeline instead of silently becoming NULL.

**/

-- Fail-fast from the very first statement.
\set ON_ERROR_STOP true

SET client_encoding TO 'UTF8';

----------------------------------------------------Extension----------------------------------------------------

-- IF NOT EXISTS keeps re-runs (and the poe pre-step that also installs it)
-- from erroring out.
CREATE EXTENSION IF NOT EXISTS fuzzystrmatch;

-----------------------------------------------------------------------------------------------------------------



----------------------------------------------------Functions----------------------------------------------------

-- Cleans all matching the given regex
CREATE OR REPLACE FUNCTION clean_all(string text, regex text) RETURNS text AS $$
		SELECT trim(regexp_replace(string, regex, '', 'g'));
$$ LANGUAGE SQL;

-- Counts the occurrences of the given substring
CREATE OR REPLACE FUNCTION count_substring(string text, substr text) RETURNS integer AS $$
    SELECT length(regexp_replace(string, concat('[^', substr, ']'), '', 'g'));
$$ LANGUAGE SQL;

-- Replaces column names to snake case
CREATE OR REPLACE PROCEDURE snake_case_column_names(my_table text)
LANGUAGE plpgsql
AS $$
	DECLARE col_name_item text;
	DECLARE new_col_name_item text;
BEGIN
	FOR col_name_item IN
  	SELECT column_name FROM information_schema.columns
        -- Restrict to the current schema so a same-named table elsewhere
        -- (e.g. a PostGIS or pgagent schema) is never touched by accident.
        WHERE table_name = my_table AND table_schema = current_schema()
  	LOOP
	  	new_col_name_item = trim(lower(regexp_replace(col_name_item, '([a-z])([A-Z])', '\1_\2', 'g')));
	  	IF new_col_name_item != col_name_item THEN
		   	EXECUTE format(
			    'ALTER TABLE %I RENAME COLUMN %I TO %I',
			    my_table,
			    col_name_item,
			    new_col_name_item
		  	);
		END IF;
	  	RAISE NOTICE '%.% -> %.%', my_table, col_name_item, my_table, new_col_name_item;
  	END LOOP;
END; $$;
-- ^ The terminating semicolon above matters: without it, psql glues the next
-- statement onto the CREATE PROCEDURE. In the old file the next statement
-- happened to be SET client_encoding, which is a *valid procedure attribute*,
-- so the bug was silent (and the session encoding was never actually set).

------------------------------------------------------------------------------------------------------------------



-- Everything below is one atomic unit: any failure rolls back the whole run.
BEGIN;

--Verify if the Dataset works
SELECT *
FROM "HousingDataRaw"
LIMIT 1;

--Create a new Table to preserve the original one in order to modify the new one.
--DROP IF EXISTS makes the pipeline re-runnable after a partial failure.
--CASCADE: address_mappings.housing_id FKs to housing_data.unique_id (see
--schema.sql). This drops only that FK constraint, not address_mappings'
--rows; sync-addresses (src/sync_addresses.sql) re-adds the constraint after
--rebuilding address_mappings against the fresh table.
DROP TABLE IF EXISTS housing_data CASCADE;

CREATE TABLE housing_data AS
(
	SELECT *
	FROM "HousingDataRaw" AS hdr
);

-- UniqueID is the natural key of the dataset; declaring it both documents
-- that and makes accidental duplicate loads fail loudly here instead of
-- corrupting downstream results.
ALTER TABLE housing_data ADD PRIMARY KEY ("UniqueID");

DO $$
BEGIN
	RAISE NOTICE 'Cleaning Nashville Data...';
END $$;

-- Cleaning
CALL snake_case_column_names('housing_data');

-- Remove extra spaces from text values.
-- The WHERE clause restricts each UPDATE to rows that actually contain
-- runs of spaces, avoiding a full-table rewrite per column.
DO $$
DECLARE col_name_item text;
BEGIN
	FOR col_name_item IN
		SELECT column_name
		FROM information_schema.columns
        WHERE table_name = 'housing_data'
          AND table_schema = current_schema()
          AND data_type = 'text'
	LOOP
	RAISE NOTICE '%', format(
		'UPDATE housing_data SET %I = regexp_replace(%I,''[ ]{2,}'', '' '', ''g'') WHERE %I ~ ''[ ]{2,}''',
		col_name_item,
		col_name_item,
		col_name_item
	);
	EXECUTE format(
		'UPDATE housing_data SET %I = regexp_replace(%I,''[ ]{2,}'', '' '', ''g'') WHERE %I ~ ''[ ]{2,}''',
		col_name_item,
		col_name_item,
		col_name_item
	);
	END LOOP;
END $$;

-- Standardise data type
ALTER TABLE housing_data
ALTER COLUMN sale_date TYPE date USING sale_date::date;

-- Keep the currency alongside the numeric amount.
ALTER TABLE housing_data
ADD currency_code TEXT DEFAULT 'USD';

-- Validate sale_price BEFORE converting: any value that is not a plain
-- price (optionally with '$', thousands separators, and decimals) aborts
-- the run instead of silently becoming NULL or losing its decimals.
DO $$
DECLARE bad_count integer;
BEGIN
	SELECT count(*) INTO bad_count
	FROM housing_data
	WHERE sale_price IS NOT NULL
	  AND REPLACE(sale_price, ',', '') !~ '^\s*\$?\s*\d+(\.\d+)?\s*$';
	IF bad_count > 0 THEN
		RAISE EXCEPTION 'sale_price: % row(s) cannot be parsed as a price - fix the source data before converting', bad_count;
	END IF;
END $$;

-- (?:...) is non-capturing so SUBSTRING returns the whole match,
-- decimals included (a capturing group would make it return only the
-- group's content).
ALTER TABLE housing_data
ALTER COLUMN sale_price TYPE NUMERIC
USING SUBSTRING(REPLACE(sale_price, ',' , ''), '\d+(?:\.\d+)?')::NUMERIC;

-- sold_as_vacant: validate, then store as a real boolean rather than
-- 'Y'/'N' text. Unexpected values abort instead of becoming NULL.
DO $$
DECLARE bad_count integer;
BEGIN
	SELECT count(*) INTO bad_count
	FROM housing_data
	WHERE sold_as_vacant IS NOT NULL
	  AND sold_as_vacant NOT IN ('Yes', 'No', 'Y', 'N');
	IF bad_count > 0 THEN
		RAISE EXCEPTION 'sold_as_vacant: % row(s) hold unexpected values - fix the source data before converting', bad_count;
	END IF;
END $$;

ALTER TABLE housing_data
ALTER COLUMN sold_as_vacant TYPE BOOLEAN
USING CASE
	WHEN sold_as_vacant IN ('Yes', 'Y') THEN true
	WHEN sold_as_vacant IN ('No', 'N') THEN false
	ELSE NULL
END;

-- Imputation provenance: Downstream users can filter non-source data via these flags.
-- Note: property_address_imputed indicates a localized heuristic fill from a sibling record.
-- owner_address_imputed indicates a high-confidence, deterministic fill based on historical name and parcel matches.
ALTER TABLE housing_data ADD COLUMN property_address_imputed boolean NOT NULL DEFAULT false;
ALTER TABLE housing_data ADD COLUMN owner_address_imputed    boolean NOT NULL DEFAULT false;

-- Populate missing values, flagging every imputed row
UPDATE housing_data hd
SET property_address = hd2.property_address,
    property_address_imputed = true
FROM housing_data hd2
WHERE hd.parcel_id = hd2.parcel_id
  AND hd.unique_id <> hd2.unique_id
  AND hd.property_address IS NULL;

UPDATE housing_data hd
SET owner_address = hd2.owner_address,
    owner_address_imputed = true
FROM housing_data hd2
WHERE hd.parcel_id = hd2.parcel_id
  AND hd.unique_id <> hd2.unique_id
  AND hd.owner_address IS NULL
  AND hd2.owner_address IS NOT NULL
  AND hd.owner_name = hd2.owner_name;

-- Correct misspelling addresses
UPDATE housing_data hd
SET property_address =
CASE
	 WHEN length(regexp_replace(hd.property_address, '[^ ]+', '', 'g')) < length(regexp_replace(hd2.property_address, '[^ ]+', '', 'g')) THEN hd2.property_address
	 ELSE hd.property_address
	END
FROM housing_data hd2
WHERE hd.parcel_id = hd2.parcel_id AND hd.unique_id <> hd2.unique_id
	AND length(regexp_replace(hd.property_address, '[^ ]+', '', 'g')) != length(regexp_replace(hd2.property_address, '[^ ]+', '', 'g'))
	AND LEVENSHTEIN(trim(regexp_replace(hd.property_address, '[0-9]+', '', 'g')), trim(regexp_replace (hd2.property_address, '[0-9]+', '', 'g')))::decimal / GREATEST(length(hd.property_address), length(hd2.property_address)) < 0.5
	AND LEVENSHTEIN(trim(regexp_replace (hd.property_address, '[0-9]+', '', 'g')), trim(regexp_replace (hd2.property_address, '[0-9]+', '', 'g'))) > 0
	AND LEVENSHTEIN(clean_all(hd.property_address, '[0-9 ]+'), clean_all(hd2.property_address, '[0-9 ]+')) = 0;

UPDATE housing_data
SET property_address = regexp_replace(property_address, '(\d+ [ABC])(\w) ', '\1 \2 ', 'g' )
WHERE property_address ~ '\d+ [ABC]\w ';

-- Targeted FALL CREEK DR repair: the correct street is held in
-- owner_address. Three guards prevent the corruption the old
-- replace(property_address, property_address, owner_address) caused:
--   1. owner_address IS NOT NULL    -> never erase a valid address
--   2. strip the trailing ', TN'    -> keep the 'NUMBER STREET, CITY'
--                                      format consistent with every other row
--   3. only touch rows that differ  -> no-op rows stay untouched
UPDATE housing_data
SET property_address = regexp_replace(owner_address, ',\s*TN\s*$', '')
WHERE property_address LIKE '%FALL CREEK DR%'
	AND owner_address IS NOT NULL
	AND regexp_replace(owner_address, ',\s*TN\s*$', '') <> property_address;

UPDATE housing_data
SET property_address = regexp_replace(property_address, '(\d+ [BC])([BC]\w+)', '\1 \2', 'g')
WHERE property_address ~ '\d+ [BC][BC]\w+';

UPDATE housing_data
SET owner_address =
			CASE
			 WHEN length(trim((regexp_match(property_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1])) > length(trim((regexp_match(owner_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1])) THEN property_address
			 ELSE owner_address
			END,
		property_address =
			CASE
			 WHEN length(trim((regexp_match(property_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1])) < length(trim((regexp_match(owner_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1])) THEN owner_address
			 ELSE property_address
			END
		WHERE length(trim((regexp_match(property_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1])) <> length(trim((regexp_match(owner_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1]))
			AND LEVENSHTEIN(trim((regexp_match(property_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1]), trim((regexp_match(owner_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1]))::decimal / GREATEST(length(trim((regexp_match(property_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1])), length(trim((regexp_match(owner_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1]))) < 0.5
			AND LEVENSHTEIN(trim((regexp_match(property_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1]), trim((regexp_match(owner_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1])) > 0
			AND LEVENSHTEIN(clean_all(((regexp_match(property_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1]), '[ ]+'), clean_all(((regexp_match(owner_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1]), '[ ]+')) = 0;

-- Remove duplicate sale records: same parcel, address, price, date and
-- legal reference is the same sale recorded twice. The earliest unique_id
-- is kept.
DELETE FROM housing_data
WHERE unique_id IN (
	SELECT unique_id FROM (
		SELECT unique_id,
		       ROW_NUMBER() OVER (
		           PARTITION BY parcel_id, property_address, sale_price, sale_date, legal_reference
		           ORDER BY unique_id
		       ) AS rn
		FROM housing_data
	) ranked
	WHERE rn > 1
);

-- End cleaning



DO $$
BEGIN
	RAISE NOTICE 'Cleaning Nashville Data complete!';
END $$;

-- Save table back into dataset
\copy housing_data TO './out/dataset.csv' DELIMITER ',' CSV HEADER;

\if :{?KEEP_TABLES}
    -- Inspection mode: keep housing_data for browsing in DBeaver etc.
    DROP TABLE "HousingDataRaw";
\else
    DROP TABLE "HousingDataRaw";
    DROP TABLE housing_data CASCADE;
\endif

COMMIT;
