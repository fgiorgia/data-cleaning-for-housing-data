/** 

This file contains the final result of my cleaning.

**/



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

------------------------------------------------------------------------------------------------------------------



-- Let the script fail as it encounters an error
\set ON_ERROR_STOP true
SET client_encoding TO 'UTF8';

--Verify if the Dataset works
SELECT *
FROM "HousingDataRaw"
LIMIT 1;

--Create a new Table to preserve the original one in order to modify the new one
CREATE TABLE housing_data AS
(
	SELECT *
	FROM "HousingDataRaw" AS hdr 
);

DO $$
BEGIN 
	RAISE NOTICE 'Cleaning Nashville Data...';
END $$;

-- Cleaning
CALL snake_case_column_names('housing_data');

-- Remove extra spaces from text values
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

-- Before changing data type from sale_price, I want to split the currency into a new table
ALTER TABLE housing_data  
ADD currency_code TEXT DEFAULT 'USD';

ALTER TABLE housing_data  
ALTER COLUMN sale_price TYPE NUMERIC USING SUBSTRING(REPLACE(sale_price, ',' , ''), '\d+')::NUMERIC;

-- Change yes and no in sold_as_vacant field
UPDATE housing_data
SET sold_as_vacant = 
	CASE 
		WHEN sold_as_vacant = 'Yes' THEN 'Y'
		WHEN sold_as_vacant = 'No' THEN 'N'
		ELSE sold_as_vacant 
	END;

-- Populate missing values
-- property_address
UPDATE housing_data hd
SET property_address = 
	NULLIF(hd2.property_address, hd.property_address) 
	FROM housing_data hd2 
	WHERE hd.parcel_id = hd2.parcel_id AND hd.unique_id <> hd2.unique_id AND hd.property_address IS NULL;

-- owner_address
UPDATE housing_data hd
SET owner_address = 
	NULLIF(hd2.property_address, hd.owner_address)
	FROM housing_data hd2 
	WHERE hd.parcel_id = hd2.parcel_id AND hd.unique_id <> hd2.unique_id AND hd.owner_address IS NULL;

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

UPDATE housing_data
SET property_address = replace(property_address, property_address, owner_address)
WHERE property_address LIKE '%FALL CREEK DR%';

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

-- End cleaning



DO $$
BEGIN 
	RAISE NOTICE 'Cleaning Nashville Data complete!';
END $$;

-- Save table back into dataset
\copy housing_data TO './out/dataset.csv' DELIMITER ',' CSV HEADER;

 DROP table "HousingDataRaw";
 DROP table housing_data;
