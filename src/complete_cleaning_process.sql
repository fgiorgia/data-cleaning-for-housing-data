/** 

This file contains all steps of the cleaning process to read and modify data

**/

----------------------------------------------------Functions----------------------------------------------------

-- Cleans all matching the given regex
CREATE FUNCTION clean_all(string varchar, regex varchar) RETURNS varchar AS $$
    SELECT trim(regexp_replace(string, regex, '', 'g'));
$$ LANGUAGE SQL;

-- Counts the occurrentces of the given substring
CREATE FUNCTION count_substring(string varchar, substr varchar) RETURNS integer AS $$
    SELECT length(regexp_replace(string, concat('[^', substr, ']'), '', 'g'));
$$ LANGUAGE SQL;

-----------------------------------------------------------------------------------------------------------------


----------------------------------------------------Examples-----------------------------------------------------

SELECT clean_all('Bob Ross  is instructivve', '[ ]+') AS answer;
SELECT count_substring('Bob Ross  is instructivve', ' ') AS answer;

-----------------------------------------------------------------------------------------------------------------


--Look at the table and check if it's working
SELECT * FROM information_schema.columns 
WHERE table_name = 'HousingDataRaw';

SELECT *
from "HousingDataRaw";

--Check if columns work
SELECT-- "﻿UniqueID ", 
"UniqueID ","ParcelID", "LandUse", "PropertyAddress", "SaleDate", "SalePrice", "LegalReference", "SoldAsVacant", "OwnerName", "OwnerAddress", "Acreage", "TaxDistrict", "LandValue", "BuildingValue", "TotalValue", "YearBuilt", "Bedrooms", "FullBath", "HalfBath"
FROM "HousingDataRaw";

SELECT *
FROM "HousingDataRaw"
WHERE COALESCE ("ParcelID", "LandUse", "PropertyAddress", "SaleDate", "LegalReference", "SoldAsVacant", "OwnerName", "OwnerAddress", "TaxDistrict") is not NULL
    or COALESCE("﻿UniqueID ", "SalePrice", "Acreage", "LandValue", "BuildingValue", "TotalValue", "YearBuilt", "Bedrooms", "FullBath", "HalfBath") is not null

 