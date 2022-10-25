-- Let the script fail as it encounters an error
\set ON_ERROR_STOP true

--Verify if the Dataset works
SELECT *
FROM "HousingDataRaw"
LIMIT 10;

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







-- End cleaning
DO $$
BEGIN 
	RAISE NOTICE 'Cleaning Nashville Data complete!';
END $$;

-- Save table back into dataset
\copy "HousingData" TO './out/dataset.csv' DELIMITER ',' CSV HEADER;
