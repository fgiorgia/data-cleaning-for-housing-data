/** 

This file contains all steps of the cleaning process to read and modify data

**/



SHOW server_version;


----------------------------------------------------Extension----------------------------------------------------

CREATE EXTENSION fuzzystrmatch;

-----------------------------------------------------------------------------------------------------------------



----------------------------------------------------Functions----------------------------------------------------

-- Cleans all matching the given regex
CREATE OR REPLACE FUNCTION clean_all(string text, regex text) RETURNS text AS $$
		SELECT trim(regexp_replace(string, regex, '', 'g'));
$$ LANGUAGE SQL;

-- Counts the occurrentces of the given substring
CREATE OR REPLACE FUNCTION count_substring(string text, substr text) RETURNS integer AS $$
    SELECT length(regexp_replace(string, concat('[^', substr, ']'), '', 'g'));
$$ LANGUAGE SQL;

-- Returns the list of columns in the table except the given ones
CREATE OR REPLACE FUNCTION get_cols_exclude(table_name_arg text, cols text[]) RETURNS text[] AS $$
    SELECT array_agg(column_name)
		FROM information_schema.columns 
		WHERE table_name = table_name_arg AND column_name != ANY(cols);
$$ LANGUAGE SQL;

-- Format an array of strings as a string of coulumn names to be used in EXECUTE
CREATE OR REPLACE FUNCTION string_array_to_cols_string(cols_array text[]) RETURNS text AS $$
    SELECT array_to_string(
      array_agg(concat('"', cols, '"')), -- Get the col name surrounded by double quotes
      ', ' -- CONNECT EACH OF them WITH a command AND SPACE
    )
    FROM UNNEST(cols_array)
    AS cols;
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
        WHERE table_name = my_table
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
END; $$

-----------------------------------------------------------------------------------------------------------------



----------------------------------------------------Examples-----------------------------------------------------

SELECT clean_all('Bob Ross  is instructivve', '[ ]+') AS answer;
SELECT count_substring('Bob Ross  is instructivve', ' ') AS answer;
SELECT get_cols_exclude('housing_data', array['unique_id']);
SELECT string_array_to_cols_string(ARRAY['parcel_id', 'acreage']);
SELECT string_array_to_cols_string(get_cols_exclude('housing_data', array['unique_id']));

-----------------------------------------------------------------------------------------------------------------



-- Look at the table and check if it's working
SELECT * 
FROM information_schema.columns 
WHERE table_name = 'HousingDataRaw';

SELECT *
FROM "HousingDataRaw";

-- Create a new table to preserve the original and modify only the new one
CREATE TABLE housing_data AS
(
	SELECT *
	FROM "HousingDataRaw" AS hdr 
); 

-- Check non-nulls, blank cells, header spaces and duplicates
SELECT 
	column_name, 
	count_substring(column_name, ' +') AS header_extraspace,
	count(*) FILTER (WHERE value ~ '  +') AS rows_extraspace,
	count(*) FILTER (WHERE value = '') AS blank_cells,
	count(value) AS non_nulls,
	count(*) - count(DISTINCT value) AS duplicates
FROM housing_data hd
CROSS JOIN LATERAL jsonb_each_text(to_jsonb(hd)) AS j(column_name, value)
GROUP BY column_name
ORDER BY non_nulls, header_extraspace DESC, rows_extraspace DESC, duplicates;

-- Replaces column names to snake case and remove spaces
CALL snake_case_column_names('housing_data');

SELECT * 
FROM housing_data hd;

-- Remove extra spaces from textual values
SELECT regexp_replace(value, '[ ]{2,}', ' ', 'g') AS no_extra_spaces
FROM housing_data hd
CROSS JOIN LATERAL jsonb_each_text(to_jsonb(hd)) AS j(column_name, value)
GROUP BY value;

DO $$
DECLARE col_name_item text;
BEGIN 
	FOR col_name_item IN 
		SELECT column_name 
		FROM information_schema.columns
        WHERE table_name = 'housing_data' AND data_type = 'text'
	LOOP
	RAISE NOTICE '%', format(
		'UPDATE housing_data SET %I = regexp_replace(%I,''[ ]{2,}'', '' '', ''g'')',
		col_name_item,
		col_name_item
	);
	EXECUTE format(
		'UPDATE housing_data SET %I = regexp_replace(%I,''[ ]{2,}'', '' '', ''g'')',
		col_name_item,
		col_name_item
	);
	END LOOP;
END $$;

-- Standardise data type
ALTER TABLE housing_data 
ALTER COLUMN sale_date TYPE date USING sale_date::date;

-- Before changing data type from sale_prices_string, split the currency into a new table
SELECT sale_price 
FROM housing_data hd 
ORDER BY sale_price;

ALTER TABLE housing_data  
ADD currency_code text DEFAULT 'USD';

SELECT SUBSTRING(REPLACE(sale_price, ',' , ''), '\d+')::numeric as num
FROM housing_data hd;

ALTER TABLE housing_data  
ALTER COLUMN sale_price TYPE NUMERIC USING SUBSTRING(REPLACE(sale_price, ',' , ''), '\d+')::NUMERIC;

SELECT *
FROM housing_data hd;

-- Change yes and no in sold_as_vacant field
SELECT sold_as_vacant, count(*)
FROM housing_data hd 
GROUP BY sold_as_vacant 
ORDER BY 2;

UPDATE housing_data
SET sold_as_vacant = 
	CASE 
		WHEN sold_as_vacant = 'Yes' THEN 'Y'
		WHEN sold_as_vacant = 'No' THEN 'N'
		ELSE sold_as_vacant 
	END;

-- Populate missing values
-- property_address
-- Check patterns (we have the same parcel_id code for some addresses)
SELECT *
FROM housing_data hd 
WHERE property_address IS NULL;

SELECT *
FROM housing_data hd 
ORDER BY parcel_id; 

SELECT a.* 
FROM housing_data hd 
JOIN (
    SELECT parcel_id, property_address, count(*) as quantity
    FROM housing_data hd2
    GROUP BY parcel_id, property_address 
) a ON hd.parcel_id = a.parcel_id AND hd.property_address <> a.property_address;

SELECT 
	DISTINCT ON (hd.unique_id) 
	hd.parcel_id, 
	hd.property_address,
	hd2.parcel_id,
	hd2.property_address,
	NULLIF(hd2.property_address, hd.property_address) 
FROM housing_data hd 
JOIN housing_data hd2
ON hd.parcel_id = hd2.parcel_id AND hd.unique_id <> hd2.unique_id
WHERE hd.property_address IS NULL;

UPDATE housing_data hd
SET property_address = 
	NULLIF(hd2.property_address, hd.property_address) 
	FROM housing_data hd2 
	WHERE hd.parcel_id = hd2.parcel_id AND hd.unique_id <> hd2.unique_id AND hd.property_address IS NULL;

-- owner_address
SELECT *
FROM housing_data hd 
WHERE owner_address IS NULL;

SELECT *
FROM housing_data hd 
ORDER BY parcel_id; 

SELECT 
	DISTINCT ON (hd.unique_id) 
	hd.parcel_id, 
	hd.owner_address,
	hd2.parcel_id,
	hd2.property_address,
	NULLIF(hd2.property_address, hd.owner_address) 
FROM housing_data hd 
JOIN housing_data hd2
ON hd.parcel_id = hd2.parcel_id AND hd.unique_id <> hd2.unique_id
WHERE hd.owner_address IS NULL;

UPDATE housing_data hd
SET owner_address = 
	NULLIF(hd2.property_address, hd.owner_address)
	FROM housing_data hd2 
	WHERE hd.parcel_id = hd2.parcel_id AND hd.unique_id <> hd2.unique_id AND hd.owner_address IS NULL;

-- Check misspelling addresses
-- property_address
SELECT
  hd.property_address,
  hd2.property_address,
  clean_all(hd.property_address, '[0-9]+ ') AS hd_address,
  clean_all(hd2.property_address, '[0-9]+ ') AS hd2_address,
  LEVENSHTEIN(clean_all(hd.property_address, '[0-9]+'), clean_all(hd2.property_address, '[0-9]+')) AS distance,
  LEVENSHTEIN(clean_all(hd.property_address, '[0-9]+'), clean_all(hd2.property_address, '[0-9]+'))::decimal / GREATEST(length(hd.property_address), length(hd2.property_address)) AS ratio
FROM housing_data hd
JOIN housing_data hd2 
ON hd.parcel_id = hd2.parcel_id AND hd.unique_id <> hd2.unique_id
WHERE LEVENSHTEIN(clean_all(hd.property_address, '[0-9]+'), clean_all(hd2.property_address, '[0-9]+'))::decimal / GREATEST(length(hd.property_address), length(hd2.property_address)) < 0.5
	AND LEVENSHTEIN(clean_all(hd.property_address, '[0-9]+'), clean_all(hd2.property_address, '[0-9]+')) > 0
  AND LEVENSHTEIN(clean_all(hd.property_address, '[0-9 ]+'), clean_all(hd2.property_address, '[0-9 ]+')) = 0
ORDER BY distance, ratio;

--Test before updating
SELECT hd.property_address, hd2.property_address, 
	CASE 
	 WHEN count_substring(hd.property_address, ' ') < count_substring(hd2.property_address, ' ') THEN hd2.property_address
	 ELSE hd.property_address
	END AS address_modify
FROM housing_data hd 
JOIN housing_data hd2 
ON hd.parcel_id = hd2.parcel_id AND hd.unique_id <> hd2.unique_id
WHERE count_substring(hd.property_address, ' ') != count_substring(hd2.property_address, ' ')
	AND LEVENSHTEIN(clean_all(hd.property_address, '[0-9]+'), clean_all(hd2.property_address, '[0-9]+'))::decimal / GREATEST(length(hd.property_address), length(hd2.property_address)) < 0.5 
	AND LEVENSHTEIN(clean_all(hd.property_address, '[0-9]+'), clean_all(hd2.property_address, '[0-9]+')) > 0 
	AND LEVENSHTEIN(clean_all(hd.property_address, '[0-9 ]+'), clean_all(hd2.property_address, '[0-9 ]+')) = 0;

-- Update the property_address column
UPDATE housing_data hd
SET property_address = 
	CASE 
	 WHEN count_substring(hd.property_address, ' ') < count_substring(hd2.property_address, ' ') THEN hd2.property_address
	 ELSE hd.property_address
	END
FROM housing_data hd2  
WHERE hd.parcel_id = hd2.parcel_id AND hd.unique_id <> hd2.unique_id
	AND count_substring(hd.property_address, ' ') != count_substring(hd2.property_address, ' ')
	AND LEVENSHTEIN(clean_all(hd.property_address, '[0-9]+'), clean_all(hd2.property_address, '[0-9]+'))::decimal / GREATEST(length(hd.property_address), length(hd2.property_address)) < 0.5 
	AND LEVENSHTEIN(clean_all(hd.property_address, '[0-9]+'), clean_all(hd2.property_address, '[0-9]+')) > 0 
	AND LEVENSHTEIN(clean_all(hd.property_address, '[0-9 ]+'), clean_all(hd2.property_address, '[0-9 ]+')) = 0;

-- Check other problems in property_address
SELECT property_address, clean_all(hd.property_address, '[0-9]+ ') AS hd_address 
FROM housing_data hd 
WHERE property_address ~ '\d+ [ABC]\w ';

-- Correct problems
SELECT property_address, regexp_replace(property_address, '(\d+ [ABC])(\w) ', ' \1 \2 ', 'g' ) AS new_address
FROM housing_data hd  
WHERE property_address ~ '\d+ [ABC]\w ';

-- Update the property_address column
UPDATE housing_data 
SET property_address = regexp_replace(property_address, '(\d+ [ABC])(\w) ', '\1 \2 ', 'g' )  
WHERE property_address ~ '\d+ [ABC]\w ';

SELECT property_address, owner_address
FROM housing_data hd
WHERE property_address LIKE '%FALL CREEK DR%';

SELECT property_address, owner_address, replace(property_address, property_address, owner_address)
FROM housing_data hd
WHERE property_address LIKE '%FALL CREEK DR%';

UPDATE housing_data
SET property_address = replace(property_address, property_address, owner_address)
WHERE property_address LIKE '%FALL CREEK DR%';

SELECT property_address, owner_address
FROM housing_data hd
WHERE property_address ~ '\d+ [BC][BC]\w+';

SELECT property_address, regexp_replace(property_address, '(\d+ [BC])([BC]\w+)', '\1 \2', 'g')
FROM housing_data hd
WHERE property_address ~ '\d+ [BC][BC]\w+';

UPDATE housing_data
SET property_address = regexp_replace(property_address, '(\d+ [BC])([BC]\w+)', '\1 \2', 'g')
WHERE property_address ~ '\d+ [BC][BC]\w+';

-- owner_address
-- Correct some addresses 
SELECT
	property_address,
	owner_address,
	trim((regexp_match(property_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1]) AS property_street,
	trim((regexp_match(owner_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1]) AS owner_street,
	LEVENSHTEIN(trim((regexp_match(property_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1]), trim((regexp_match(owner_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1])) as distance,
	LEVENSHTEIN(trim((regexp_match(property_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1]), trim((regexp_match(owner_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1]))::decimal / GREATEST(length(trim((regexp_match(property_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1])), length(trim((regexp_match(owner_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1]))) AS ratio
FROM housing_data hd 
WHERE length(trim((regexp_match(property_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1])) <> length(trim((regexp_match(owner_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1]))
	AND LEVENSHTEIN(trim((regexp_match(property_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1]), trim((regexp_match(owner_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1]))::decimal / GREATEST(length(trim((regexp_match(property_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1])), length(trim((regexp_match(owner_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1]))) < 0.5 
	AND LEVENSHTEIN(trim((regexp_match(property_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1]), trim((regexp_match(owner_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1])) > 0 
	AND LEVENSHTEIN(clean_all(((regexp_match(property_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1]), '[ ]+'), clean_all(((regexp_match(owner_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1]), '[ ]+')) = 0
ORDER BY distance, ratio;

-- Test before updating
SELECT property_address, owner_address, 
	CASE 
	 WHEN length(trim((regexp_match(property_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1])) < length(trim((regexp_match(owner_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1])) THEN owner_address 
	 ELSE property_address
	END AS address_modify
FROM housing_data hd 
WHERE length(trim((regexp_match(property_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1])) <> length(trim((regexp_match(owner_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1]))
	AND LEVENSHTEIN(trim((regexp_match(property_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1]), trim((regexp_match(owner_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1]))::decimal / GREATEST(length(trim((regexp_match(property_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1])), length(trim((regexp_match(owner_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1]))) < 0.5 
	AND LEVENSHTEIN(trim((regexp_match(property_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1]), trim((regexp_match(owner_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1])) > 0 
	AND LEVENSHTEIN(clean_all(((regexp_match(property_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1]), '[ ]+'), clean_all(((regexp_match(owner_address, '(^[A-Z ]+[A-Z0-9 ]+| [A-Z0-9-]+[A-ZA-Z0-9 ]+|^[0-9]{1,2}[A-Z ]+),'))[1]), '[ ]+')) = 0;

-- Update the owner_address column
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
