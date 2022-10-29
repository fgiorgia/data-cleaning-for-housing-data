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
SELECT * 
FROM information_schema.columns 
WHERE table_name = 'HousingDataRaw';

SELECT *
FROM "HousingDataRaw";

--Check non-nulls, blank cells and header spaces
SELECT column_name, count(value) AS non_nulls, count(*) FILTER (WHERE value = '') AS blank_cells, count_substring(column_name, ' ') AS headers_space
FROM "HousingDataRaw"  nh
  CROSS JOIN LATERAL jsonb_each_text(jsonb_strip_nulls(to_jsonb(nh))) AS j(column_name, value)
GROUP BY column_name
ORDER BY non_nulls, blank_cells DESC;

    or COALESCE("﻿UniqueID ", "SalePrice", "Acreage", "LandValue", "BuildingValue", "TotalValue", "YearBuilt", "Bedrooms", "FullBath", "HalfBath") is not null

 