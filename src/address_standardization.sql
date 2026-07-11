/**
 * Address standardization functions for housing data
 * These functions help prepare addresses for geocoding by standardizing formats
 */

-- Function to standardize address abbreviations (rd → road, st → street, etc.)
CREATE OR REPLACE FUNCTION standardize_address_abbreviations(address text) 
RETURNS text AS $$
DECLARE
    result text;
BEGIN
    -- Convert the address to uppercase for consistent processing
    result := upper(address);
    
    -- Common street type abbreviations with explicit handling of what follows
    result := regexp_replace(result, ' RD,', ' ROAD,', 'g');
    result := regexp_replace(result, ' RD ', ' ROAD ', 'g');
    result := regexp_replace(result, ' RD$', ' ROAD', 'g');
    
    result := regexp_replace(result, ' ST,', ' STREET,', 'g');
    result := regexp_replace(result, ' ST ', ' STREET ', 'g');
    result := regexp_replace(result, ' ST$', ' STREET', 'g');
    
    result := regexp_replace(result, ' AVE,', ' AVENUE,', 'g');
    result := regexp_replace(result, ' AVE ', ' AVENUE ', 'g');
    result := regexp_replace(result, ' AVE$', ' AVENUE', 'g');
    
    result := regexp_replace(result, ' BLVD,', ' BOULEVARD,', 'g');
    result := regexp_replace(result, ' BLVD ', ' BOULEVARD ', 'g');
    result := regexp_replace(result, ' BLVD$', ' BOULEVARD', 'g');
    
    result := regexp_replace(result, ' DR,', ' DRIVE,', 'g');
    result := regexp_replace(result, ' DR ', ' DRIVE ', 'g');
    result := regexp_replace(result, ' DR$', ' DRIVE', 'g');
    
    result := regexp_replace(result, ' LN,', ' LANE,', 'g');
    result := regexp_replace(result, ' LN ', ' LANE ', 'g');
    result := regexp_replace(result, ' LN$', ' LANE', 'g');
    
    result := regexp_replace(result, ' CT,', ' COURT,', 'g');
    result := regexp_replace(result, ' CT ', ' COURT ', 'g');
    result := regexp_replace(result, ' CT$', ' COURT', 'g');
    
    result := regexp_replace(result, ' CIR,', ' CIRCLE,', 'g');
    result := regexp_replace(result, ' CIR ', ' CIRCLE ', 'g');
    result := regexp_replace(result, ' CIR$', ' CIRCLE', 'g');
    
    result := regexp_replace(result, ' PLZ,', ' PLAZA,', 'g');
    result := regexp_replace(result, ' PLZ ', ' PLAZA ', 'g');
    result := regexp_replace(result, ' PLZ$', ' PLAZA', 'g');
    
    result := regexp_replace(result, ' SQ,', ' SQUARE,', 'g');
    result := regexp_replace(result, ' SQ ', ' SQUARE ', 'g');
    result := regexp_replace(result, ' SQ$', ' SQUARE', 'g');
    
    result := regexp_replace(result, ' TER,', ' TERRACE,', 'g');
    result := regexp_replace(result, ' TER ', ' TERRACE ', 'g');
    result := regexp_replace(result, ' TER$', ' TERRACE', 'g');
    
    result := regexp_replace(result, ' PKWY,', ' PARKWAY,', 'g');
    result := regexp_replace(result, ' PKWY ', ' PARKWAY ', 'g');
    result := regexp_replace(result, ' PKWY$', ' PARKWAY', 'g');
    
    result := regexp_replace(result, ' HWY,', ' HIGHWAY,', 'g');
    result := regexp_replace(result, ' HWY ', ' HIGHWAY ', 'g');
    result := regexp_replace(result, ' HWY$', ' HIGHWAY', 'g');
    
    -- Directional abbreviations
    result := regexp_replace(result, ' N ', ' NORTH ', 'g');
    result := regexp_replace(result, ' S ', ' SOUTH ', 'g');
    result := regexp_replace(result, ' E ', ' EAST ', 'g');
    result := regexp_replace(result, ' W ', ' WEST ', 'g');
    result := regexp_replace(result, ' NE ', ' NORTHEAST ', 'g');
    result := regexp_replace(result, ' NW ', ' NORTHWEST ', 'g');
    result := regexp_replace(result, ' SE ', ' SOUTHEAST ', 'g');
    result := regexp_replace(result, ' SW ', ' SOUTHWEST ', 'g');
    
    -- Clean up multiple spaces
    result := regexp_replace(result, '\s+', ' ', 'g');
    
    RETURN trim(result);
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- Function to extract house number from an address
CREATE OR REPLACE FUNCTION extract_house_number(address text)
RETURNS text AS $$
DECLARE
    house_number text;
BEGIN
    -- Try to extract a house number (typically at the beginning of the address)
    house_number := substring(address from '^\s*(\d+(?:-\d+)?)\s');
    RETURN house_number;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- Function to extract street name from an address
CREATE OR REPLACE FUNCTION extract_street_name(address text)
RETURNS text AS $$
DECLARE
    street_name text;
BEGIN
    -- Remove house number from beginning
    street_name := regexp_replace(address, '^\s*\d+(?:-\d+)?\s+', '');
    
    -- Remove everything after the first comma if it exists
    IF position(',' IN street_name) > 0 THEN
        street_name := substring(street_name from 1 for position(',' IN street_name) - 1);
    END IF;
    
    RETURN trim(street_name);
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- Function to extract city from an address
CREATE OR REPLACE FUNCTION extract_city(address text)
RETURNS text AS $$
DECLARE
    city text;
BEGIN
    -- Try to extract city (assuming it's after the first comma)
    IF position(',' IN address) > 0 THEN
        city := substring(address from position(',' IN address) + 1);
        
        -- Remove anything after the second comma if it exists
        IF position(',' IN city) > 0 THEN
            city := substring(city from 1 for position(',' IN city) - 1);
        END IF;
        
        RETURN trim(city);
    END IF;
    
    RETURN NULL;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- Comprehensive function to standardize and structure address components
CREATE OR REPLACE FUNCTION parse_address_components(address text)
RETURNS TABLE(
    house_number text,
    street_name text,
    city text,
    standardized_address text
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        extract_house_number(address),
        extract_street_name(address),
        extract_city(address),
        standardize_address_abbreviations(address);
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- Create a demo view to show the parsed components
CREATE OR REPLACE VIEW address_components_view AS
SELECT 
    address_id,
    address,
    (parse_address_components(address)).*
FROM 
    unique_addresses
WHERE 
    address IS NOT NULL
LIMIT 100;

ALTER TABLE unique_addresses 
ADD COLUMN IF NOT EXISTS address_standardized TEXT;

-- Use this to apply standardization to addresses before geocoding
UPDATE unique_addresses
SET address_standardized = standardize_address_abbreviations(address)
WHERE address IS NOT NULL;