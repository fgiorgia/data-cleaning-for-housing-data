/**

Data-quality maintenance for the restored geocoded_housing database.
Run by `uv run poe data-quality-maintenance`.

Two curated fixes, found via the data-quality dashboard:

1. Remove duplicate sale records - the same sale recorded twice (same
   parcel, address, price, date and legal reference; the same definition
   src/cleaning.sql applies to the cleaning pipeline). The migration that
   built this database never deduplicated. The lowest unique_id of each
   group is kept; the duplicates' address_mappings and data_quality_issues
   rows are removed with them so no orphans remain.

2. Record placeholder addresses in data_quality_issues (non-destructive).
   Addresses whose house number is 0 ("0 10TH AVE N", or a bare "0") are
   placeholders, not real street numbers - geocoders resolve them to a
   street centroid, so their coordinates look valid but are wrong. They are
   flagged for review instead of modified.

Idempotent: a re-run deletes nothing (no duplicates remain) and inserts
nothing (NOT EXISTS guard on the issue rows).

**/

\set ON_ERROR_STOP true

SET client_encoding TO 'UTF8';

BEGIN;

-- 1) Duplicate sale records: keep the earliest unique_id per group.
CREATE TEMP TABLE duplicate_sales ON COMMIT DROP AS
SELECT unique_id
FROM (
	SELECT unique_id,
	       ROW_NUMBER() OVER (
	           PARTITION BY parcel_id, property_address, sale_price, sale_date, legal_reference
	           ORDER BY unique_id
	       ) AS rn
	FROM housing_data
) ranked
WHERE rn > 1;

DELETE FROM address_mappings am
USING duplicate_sales d
WHERE am.housing_id = d.unique_id;

DELETE FROM data_quality_issues qi
USING duplicate_sales d
WHERE qi.unique_id = d.unique_id;

DELETE FROM housing_data hd
USING duplicate_sales d
WHERE hd.unique_id = d.unique_id;

-- 2) Placeholder house numbers, flagged for review.
INSERT INTO data_quality_issues (unique_id, address1, address2, issue_type)
SELECT hd.unique_id,
       hd.property_address,
       hd.owner_address,
       'Placeholder House Number (0)'
FROM housing_data hd
WHERE hd.property_address ~ '^0( |$)'
  AND NOT EXISTS (
      SELECT 1
      FROM data_quality_issues qi
      WHERE qi.unique_id = hd.unique_id
        AND qi.issue_type = 'Placeholder House Number (0)'
  );

-- Summary for the operator.
SELECT (SELECT count(*) FROM housing_data)            AS housing_rows,
       (SELECT count(*) FROM (
            SELECT 1
            FROM housing_data
            GROUP BY parcel_id, property_address, sale_price, sale_date, legal_reference
            HAVING count(*) > 1
        ) still_duplicated)                            AS remaining_duplicate_groups,
       (SELECT count(*) FROM data_quality_issues
        WHERE issue_type = 'Placeholder House Number (0)') AS placeholder_issues;

COMMIT;
