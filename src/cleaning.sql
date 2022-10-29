/** 

This file contains the final result of my cleaning.

**/

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
SELECT column_name 
FROM information_schema.columns 
WHERE table_name = 'HousingDataRaw' AND ordinal_position = 1;

ALTER TABLE "HousingData"
RENAME COLUMN "UniqueID " TO "UniqueID";





-- End cleaning
DO $$
BEGIN 
	RAISE NOTICE 'Cleaning Nashville Data complete!';
END $$;

-- Save table back into dataset
\copy "HousingData" TO './out/dataset.csv' DELIMITER ',' CSV HEADER;

DROP table "HousingDataRaw";
DROP table "HousingData";
