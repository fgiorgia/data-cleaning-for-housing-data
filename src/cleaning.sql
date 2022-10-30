/** 

This file contains the final result of my cleaning.

**/

-- Functions
-- Replaces column names to snake case and remove spaces
CREATE OR REPLACE PROCEDURE snake_case_column_names(my_table varchar) 
LANGUAGE plpgsql    
AS $$
	DECLARE col_name_item varchar(250);
	DECLARE new_col_name_item varchar(250);
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

-- Let the script fail as it encounters an error
\set ON_ERROR_STOP true
SET client_encoding TO 'UTF8';

--Verify if the Dataset works
SELECT *
FROM "HousingDataRaw"
LIMIT 1;

--Create a new Table to preserve the original one in order to modify the new one
CREATE TABLE "HousingData" AS
(
	SELECT *
	FROM "HousingDataRaw" AS hdr 
);

DO $$
BEGIN 
	RAISE NOTICE 'Cleaning Nashville Data...';
END $$;

-- Cleaning

CALL snake_case_column_names('HousingData');

-- Remove extra spaces from text values
DO $$
DECLARE col_name_item varchar(250);
BEGIN 
	FOR col_name_item IN 
		SELECT column_name 
		FROM information_schema.columns
        WHERE table_name = 'HousingData' AND data_type = 'text'
	LOOP
	RAISE NOTICE '%', format(
		'UPDATE "HousingData" SET %I = regexp_replace(%I,''[ ]{2,}'', '' '', ''g'')',
		col_name_item,
		col_name_item
	);
	EXECUTE format(
		'UPDATE "HousingData" SET %I = regexp_replace(%I,''[ ]{2,}'', '' '', ''g'')',
		col_name_item,
		col_name_item
	);
	END LOOP;
END $$;





-- End cleaning
DO $$
BEGIN 
	RAISE NOTICE 'Cleaning Nashville Data complete!';
END $$;

-- Save table back into dataset
\copy "HousingData" TO './out/dataset.csv' DELIMITER ',' CSV HEADER;

DROP table "HousingDataRaw";
DROP table "HousingData";
