/** 

This file contains all steps of the cleaning process to read and modify data

**/

----------------------------------------------------Functions----------------------------------------------------

-- Cleans all matching the given regex
CREATE OR REPLACE FUNCTION clean_all(string varchar, regex varchar) RETURNS varchar AS $$
    SELECT trim(regexp_replace(string, regex, '', 'g'));
$$ LANGUAGE SQL;

-- Counts the occurrentces of the given substring
CREATE OR REPLACE FUNCTION count_substring(string varchar, substr varchar) RETURNS integer AS $$
    SELECT length(regexp_replace(string, concat('[^', substr, ']'), '', 'g'));
$$ LANGUAGE SQL;

-- Replaces column names to snake case
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

-----------------------------------------------------------------------------------------------------------------



----------------------------------------------------Examples-----------------------------------------------------

SELECT clean_all('Bob Ross  is instructivve', '[ ]+') AS answer;
SELECT count_substring('Bob Ross  is instructivve', ' ') AS answer;

-----------------------------------------------------------------------------------------------------------------


-- Look at the table and check if it's working
SELECT * 
FROM information_schema.columns 
WHERE table_name = 'HousingDataRaw';

SELECT *
FROM "HousingDataRaw";

-- Create a new table to preserve the original and modify only the new one
CREATE TABLE "HousingData" AS
(
	SELECT *
	FROM "HousingDataRaw" AS hdr 
);

-- Check non-nulls, blank cells, header spaces and duplicates
SELECT 
	column_name, 
	count_substring(column_name, ' +') AS header_extraspace,
	count(value) AS non_nulls,  
	count(*) FILTER (WHERE value ~ '  +') AS rows_extraspace,
	count(*) - count(DISTINCT value) AS duplicates
FROM "HousingData" nh
CROSS JOIN LATERAL jsonb_each_text(jsonb_strip_nulls(to_jsonb(nh))) AS j(column_name, value)
GROUP BY column_name
ORDER BY non_nulls, header_extraspace DESC, rows_extraspace DESC;

-- Replaces column names to snake case and remove spaces
CALL snake_case_column_names('HousingData');

SELECT * 
FROM "HousingData" hdr;

-- Remove extra spaces from text values
SELECT regexp_replace(value, '[ ]{2,}', ' ') AS removal_spaces
FROM "HousingData"  nh
CROSS JOIN LATERAL jsonb_each_text(jsonb_strip_nulls(to_jsonb(nh))) AS j(column_name, value)
GROUP BY value

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



 