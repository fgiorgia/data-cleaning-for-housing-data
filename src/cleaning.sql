-- Check if this is Windows
select version();
DECLARE is_windows CONSTANT datatype [ NOT NULL ] [ { DEFAULT | := } initial_value ]

-- Create DB table
CREATE TABLE "HousingDataRaw"
(
	"UniqueID" VARCHAR(250),
    "ParcelID" VARCHAR(250), 
    "LandUse" VARCHAR(250), 
    "PropertyAddress" VARCHAR(250), 
    "SaleDate" VARCHAR(250), 
    "SalePricesString" VARCHAR(250), 
    "LegalReference" VARCHAR(250), 
    "SoldAsVacant" VARCHAR(250), 
    "OwnerName" VARCHAR(250), 
    "OwnerAddress" VARCHAR(250), 
    "Acreage" VARCHAR(250), 
    "TaxDistrict" VARCHAR(250), 
    "LandValue" VARCHAR(250), 
    "BuildingValue" VARCHAR(250), 
    "TotalValue" VARCHAR(250), 
    "YearBuilt" VARCHAR(250), 
    "Bedrooms" VARCHAR(250), 
    "FullBath" VARCHAR(250), 
    "HalfBath"  VARCHAR(250),
    "DELETE_ME" VARCHAR(10)
);

-- Load dataset into our table
\copy "HousingDataRaw" FROM './data/dataset.csv' DELIMITER '|' CSV HEADER;

--Verify if the Dataset works
-- SELECT "UniqueID ", "ParcelID", "LandUse", "PropertyAddress", "SaleDate", "SalePricesString", "LegalReference", "SoldAsVacant", "OwnerName", "OwnerAddress", "Acreage", "TaxDistrict", "LandValue", "BuildingValue", "TotalValue", "YearBuilt", "Bedrooms", "FullBath", "HalfBath"
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
