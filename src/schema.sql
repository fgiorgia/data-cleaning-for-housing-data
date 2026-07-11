--
-- Authoritative schema for the Nashville housing database.
--
-- Originally generated from a pg_restore --schema-only dump of
-- data/migration_dump.backup; that backup file no longer exists (removed
-- when the project moved to a durable, upsert-only geocode cache instead of
-- a shipped binary dump -- see RUNBOOK.md §2), so this file can no longer be
-- regenerated the same way. It remains the source of truth for the full
-- enriched system's DDL (PostGIS geometry, geocoding tables,
-- address-parsing functions) -- consult it, don't hand-edit it without
-- reconciling against the live `geocoded_housing` database.
--
-- Dumped from database version 17.4
-- Dumped by pg_dump version 17.4

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: pgagent; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA pgagent;


--
-- Name: SCHEMA pgagent; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA pgagent IS 'pgAgent system tables';


--
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--

-- *not* creating schema, since initdb creates it


--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA public IS '';


--
-- Name: fuzzystrmatch; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS fuzzystrmatch WITH SCHEMA public;


--
-- Name: EXTENSION fuzzystrmatch; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION fuzzystrmatch IS 'determine similarities and distance between strings';


--
-- Name: pgagent; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pgagent WITH SCHEMA pgagent;


--
-- Name: EXTENSION pgagent; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pgagent IS 'A PostgreSQL job scheduler';


--
-- Name: postgis; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS postgis WITH SCHEMA public;


--
-- Name: EXTENSION postgis; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION postgis IS 'PostGIS geometry and geography spatial types and functions';


--
-- Name: clean_all(text, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.clean_all(string text, regex text) RETURNS text
    LANGUAGE sql
    AS $$
		SELECT trim(regexp_replace(string, regex, '', 'g'));
$$;


--
-- Name: count_substring(text, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.count_substring(string text, substr text) RETURNS integer
    LANGUAGE sql
    AS $$
    SELECT length(regexp_replace(string, concat('[^', substr, ']'), '', 'g'));
$$;


--
-- Name: extract_city(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.extract_city(address text) RETURNS text
    LANGUAGE plpgsql IMMUTABLE
    AS $$
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
$$;


--
-- Name: extract_house_number(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.extract_house_number(address text) RETURNS text
    LANGUAGE plpgsql IMMUTABLE
    AS $$
DECLARE
    house_number text;
BEGIN
    -- Try to extract a house number (typically at the beginning of the address)
    house_number := substring(address from '^\s*(\d+(?:-\d+)?)\s');
    RETURN house_number;
END;
$$;


--
-- Name: extract_street_name(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.extract_street_name(address text) RETURNS text
    LANGUAGE plpgsql IMMUTABLE
    AS $$
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
$$;


--
-- Name: get_cols_exclude(text, text[]); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.get_cols_exclude(table_name_arg text, cols text[]) RETURNS text[]
    LANGUAGE sql
    AS $$
    SELECT array_agg(column_name)
		FROM information_schema.columns 
		WHERE table_name = table_name_arg AND column_name != ANY(cols);
$$;


--
-- Name: impute_address_with_criteria(text, text, text, text, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.impute_address_with_criteria(target_column text, source_column text, condition text DEFAULT 'TRUE'::text, flag_column text DEFAULT 'address_was_imputed'::text, table_name text DEFAULT 'housing_data'::text) RETURNS integer
    LANGUAGE plpgsql
    AS $$
DECLARE
    flag_column_exists BOOLEAN;
    add_column_query TEXT;
    update_query TEXT;
    records_updated INTEGER;
BEGIN
    -- Check if the flag column exists
    SELECT EXISTS (
        SELECT 1
        FROM information_schema.COLUMNS AS info_cols
        WHERE info_cols.table_name = impute_address_with_criteria.table_name
        AND info_cols.column_name = impute_address_with_criteria.flag_column
    ) INTO flag_column_exists;
    
    -- Create the flag column if it doesn't exist
    IF NOT flag_column_exists THEN
        add_column_query := format('ALTER TABLE %I ADD COLUMN %I BOOLEAN DEFAULT FALSE', 
                                  table_name, flag_column);
        EXECUTE add_column_query;
        RAISE NOTICE 'Created flag column %', flag_column;
    END IF;
    
    -- Build and execute the update query with custom condition
    update_query := format(
        'UPDATE %I SET %I = %I, %I = TRUE 
         WHERE %I IS NULL AND %I IS NOT NULL AND %s
         RETURNING 1',
        table_name, target_column, source_column, flag_column,
        target_column, source_column, condition
    );
    
    EXECUTE update_query INTO records_updated;
    
    RAISE NOTICE 'Imputed % records from % to % with condition: %', 
                 records_updated, source_column, target_column, condition;
    
    RETURN records_updated;
END;
$$;


--
-- Name: match_address(text, double precision); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.match_address(in_address text, in_threshold double precision DEFAULT 0.8) RETURNS TABLE(osm_id text, full_address text, street_number text, street_name text, latitude numeric, longitude numeric, confidence double precision)
    LANGUAGE plpgsql
    AS $$
DECLARE
    street_part text;
BEGIN
    -- Extract street part (before any comma)
    street_part := split_part(in_address, ',', 1);
    
    RETURN QUERY
    WITH candidate_addresses AS (
        SELECT 
            a.osm_id, 
            a.full_address, 
            a.street_number, 
            a.street_name,
            a.latitude, 
            a.longitude,
            -- Street name match score using Levenshtein
            (1 - LEVENSHTEIN(
                trim(regexp_replace(street_part, '[0-9]+', '', 'g')), 
                trim(regexp_replace(CONCAT(a.street_name, ' ', COALESCE(a.street_suffix, '')), '[0-9]+', '', 'g'))
            )::decimal / 
            GREATEST(
                length(trim(regexp_replace(street_part, '[0-9]+', '', 'g'))), 
                length(trim(regexp_replace(CONCAT(a.street_name, ' ', COALESCE(a.street_suffix, '')), '[0-9]+', '', 'g')))
            )) AS confidence
        FROM 
            osm_addresses a
        WHERE 
            -- Prefilter candidates
            a.street_name ILIKE '%' || REPLACE(REPLACE(REPLACE(street_part, ' ', '%'), ',', ''), '.', '') || '%'
    )
    SELECT 
        osm_id, 
        full_address, 
        street_number, 
        street_name,
        latitude, 
        longitude,
        confidence
    FROM 
        candidate_addresses
    WHERE 
        confidence >= in_threshold
    ORDER BY 
        confidence DESC
    LIMIT 1;
END;
$$;


--
-- Name: normalize_state(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.normalize_state(state_text text) RETURNS text
    LANGUAGE plpgsql IMMUTABLE
    AS $$
BEGIN
    -- Convert the state to uppercase
    state_text := upper(trim(state_text));
    
    -- Handle common variations
    IF state_text IN ('TENNESSEE', 'TENN', 'TENN.', 'TN.', 'TENNESSE') THEN
        RETURN 'TN';
    END IF;
    
    -- If it's already TN, return as is
    IF state_text = 'TN' THEN
        RETURN state_text;
    END IF;
    
    -- Default to TN for Nashville housing data
    RETURN 'TN';
END;
$$;


--
-- Name: parse_address_components(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.parse_address_components(address text) RETURNS TABLE(house_number text, street_name text, city text, standardized_address text)
    LANGUAGE plpgsql IMMUTABLE
    AS $$
BEGIN
    RETURN QUERY
    SELECT 
        extract_house_number(address),
        extract_street_name(address),
        extract_city(address),
        standardize_address_abbreviations(address);
END;
$$;


--
-- Name: snake_case_column_names(text); Type: PROCEDURE; Schema: public; Owner: -
--

CREATE PROCEDURE public.snake_case_column_names(IN my_table text)
    LANGUAGE plpgsql
    AS $$
  DECLARE col_name_item text;
  DECLARE new_col_name_item text;
  DECLARE special_case boolean;
BEGIN 
  FOR col_name_item IN
    SELECT column_name FROM information_schema.columns
        WHERE table_name = my_table
  LOOP
    special_case := false;
    
    -- Handle special cases first
    IF col_name_item = 'suite/ condo   #' THEN
      new_col_name_item := 'suite_or_condo';
      special_case := true;
    ELSIF col_name_item = 'Unnamed: 0' THEN
      new_col_name_item := 'unique_id';
      special_case := true;
    -- Add more special cases as needed
    END IF;
    
    -- If not a special case, apply standard snake_case rules
    IF NOT special_case THEN
      -- Convert to lowercase
      new_col_name_item := lower(col_name_item);
      
      -- Replace slashes with "_or_"
      new_col_name_item := regexp_replace(new_col_name_item, '/', '_or_', 'g');
      
      -- Remove hash symbols and other non-alphanumeric characters except spaces
      new_col_name_item := regexp_replace(new_col_name_item, '[#]', '', 'g');
      
      -- Replace camel case (insert underscore between lowercase and uppercase letters)
      new_col_name_item := regexp_replace(new_col_name_item, '([a-z])([A-Z])', '\1_\2', 'g');
      
      -- Replace multiple spaces with a single space
      new_col_name_item := regexp_replace(new_col_name_item, '\s+', ' ', 'g');
      
      -- Replace spaces with underscores
      new_col_name_item := regexp_replace(new_col_name_item, '\s', '_', 'g');
      
      -- Replace multiple underscores with a single underscore
      new_col_name_item := regexp_replace(new_col_name_item, '_+', '_', 'g');
      
      -- Trim leading/trailing underscores
      new_col_name_item := trim(both '_' from new_col_name_item);
    END IF;
    
    -- Only rename if the name has changed
    IF new_col_name_item != col_name_item THEN
      EXECUTE format(
        'ALTER TABLE %I RENAME COLUMN %I TO %I',
        my_table,
        col_name_item,
        new_col_name_item
      );
      RAISE NOTICE '%.% -> %.%', my_table, col_name_item, my_table, new_col_name_item;
    END IF;
  END LOOP;
END; $$;


--
-- Name: standardize_address_abbreviations(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.standardize_address_abbreviations(address text) RETURNS text
    LANGUAGE plpgsql IMMUTABLE
    AS $_$
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
$_$;


--
-- Name: string_array_to_cols_string(text[]); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.string_array_to_cols_string(cols_array text[]) RETURNS text
    LANGUAGE sql
    AS $$
    SELECT array_to_string(
      array_agg(concat('"', cols, '"')), -- Get the col name surrounded by double quotes
      ', ' -- CONNECT EACH OF them WITH a command AND SPACE
    )
    FROM UNNEST(cols_array)
    AS cols;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: unique_addresses; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.unique_addresses (
    address_id integer NOT NULL,
    address text NOT NULL,
    city text,
    state_validated boolean DEFAULT false,
    validation_notes text,
    latitude double precision,
    longitude double precision,
    corrected_address text,
    confidence double precision,
    source character varying(10),
    status character varying(20),
    geocoded_at timestamp without time zone,
    last_updated timestamp without time zone,
    address_hash character varying(32),
    geom public.geometry(Point,4326),
    address_standardized text
);


--
-- Name: address_components_view; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.address_components_view AS
 SELECT address_id,
    address,
    (public.parse_address_components(address)).house_number AS house_number,
    (public.parse_address_components(address)).street_name AS street_name,
    (public.parse_address_components(address)).city AS city,
    (public.parse_address_components(address)).standardized_address AS standardized_address
   FROM public.unique_addresses
  WHERE (address IS NOT NULL)
 LIMIT 100;


--
-- Name: address_correction_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.address_correction_log (
    id integer NOT NULL,
    address_id integer NOT NULL,
    original_value text,
    new_value text,
    field_changed character varying(50),
    changed_by character varying(100),
    changed_at timestamp without time zone DEFAULT now(),
    reason text
);


--
-- Name: address_correction_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.address_correction_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: address_correction_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.address_correction_log_id_seq OWNED BY public.address_correction_log.id;


--
-- Name: address_mappings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.address_mappings (
    mapping_id integer NOT NULL,
    housing_id integer NOT NULL,
    address_id integer NOT NULL,
    address_type text NOT NULL
);


--
-- Name: address_mappings_mapping_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.address_mappings_mapping_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: address_mappings_mapping_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.address_mappings_mapping_id_seq OWNED BY public.address_mappings.mapping_id;


--
-- Name: api_usage; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.api_usage (
    id integer NOT NULL,
    api_name character varying(10) NOT NULL,
    request_date date NOT NULL,
    request_count integer DEFAULT 0,
    last_updated timestamp without time zone DEFAULT now()
);


--
-- Name: api_usage_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.api_usage_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: api_usage_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.api_usage_id_seq OWNED BY public.api_usage.id;


--
-- Name: data_quality_issues; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.data_quality_issues (
    unique_id bigint,
    address1 text,
    address2 text,
    issue_type text
);


--
-- Name: geocoding_status; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.geocoding_status AS
 SELECT count(*) AS total_addresses,
    count(
        CASE
            WHEN ((latitude IS NOT NULL) AND (longitude IS NOT NULL)) THEN 1
            ELSE NULL::integer
        END) AS geocoded_count,
    count(
        CASE
            WHEN ((latitude IS NULL) OR (longitude IS NULL)) THEN 1
            ELSE NULL::integer
        END) AS not_geocoded_count,
    count(
        CASE
            WHEN ((source)::text = 'OSM'::text) THEN 1
            ELSE NULL::integer
        END) AS osm_count,
    count(
        CASE
            WHEN ((source)::text = 'HERE'::text) THEN 1
            ELSE NULL::integer
        END) AS here_count,
    ((((count(
        CASE
            WHEN ((latitude IS NOT NULL) AND (longitude IS NOT NULL)) THEN 1
            ELSE NULL::integer
        END))::numeric / (NULLIF(count(*), 0))::numeric) * (100)::numeric))::numeric(5,2) AS geocoded_percentage
   FROM public.unique_addresses;


--
-- Name: housing_data; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.housing_data (
    unique_id bigint NOT NULL,
    parcel_id text,
    land_use text,
    property_address text,
    suite_or_condo text,
    property_city text,
    sale_date date,
    sale_price bigint,
    legal_reference text,
    sold_as_vacant text,
    multiple_parcels_involved_in_sale text,
    owner_name text,
    owner_address text,
    owner_city text,
    owner_state text,
    acreage double precision,
    tax_district text,
    neighborhood double precision,
    image text,
    land_value double precision,
    building_value double precision,
    total_value double precision,
    finished_area double precision,
    foundation_type text,
    year_built double precision,
    exterior_wall text,
    grade text,
    bedrooms double precision,
    full_bath double precision,
    half_bath double precision,
    currency_code text DEFAULT 'USD'::text,
    sale_price_numeric numeric
);


--
-- Name: unique_addresses_address_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.unique_addresses_address_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: unique_addresses_address_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.unique_addresses_address_id_seq OWNED BY public.unique_addresses.address_id;


--
-- Name: address_correction_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.address_correction_log ALTER COLUMN id SET DEFAULT nextval('public.address_correction_log_id_seq'::regclass);


--
-- Name: address_mappings mapping_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.address_mappings ALTER COLUMN mapping_id SET DEFAULT nextval('public.address_mappings_mapping_id_seq'::regclass);


--
-- Name: api_usage id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_usage ALTER COLUMN id SET DEFAULT nextval('public.api_usage_id_seq'::regclass);


--
-- Name: unique_addresses address_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.unique_addresses ALTER COLUMN address_id SET DEFAULT nextval('public.unique_addresses_address_id_seq'::regclass);


--
-- Name: address_correction_log address_correction_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.address_correction_log
    ADD CONSTRAINT address_correction_log_pkey PRIMARY KEY (id);


--
-- Name: address_mappings address_mappings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.address_mappings
    ADD CONSTRAINT address_mappings_pkey PRIMARY KEY (mapping_id);


--
-- Name: api_usage api_usage_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_usage
    ADD CONSTRAINT api_usage_pkey PRIMARY KEY (id);


--
-- Name: housing_data housing_data_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.housing_data
    ADD CONSTRAINT housing_data_pkey PRIMARY KEY (unique_id);


--
-- Name: unique_addresses unique_addresses_address_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.unique_addresses
    ADD CONSTRAINT unique_addresses_address_key UNIQUE (address);


--
-- Name: unique_addresses unique_addresses_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.unique_addresses
    ADD CONSTRAINT unique_addresses_pkey PRIMARY KEY (address_id);


--
-- Name: idx_address_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_address_hash ON public.unique_addresses USING btree (address_hash);


--
-- Name: idx_address_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_address_id ON public.address_mappings USING btree (address_id);


--
-- Name: idx_api_usage_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_api_usage_unique ON public.api_usage USING btree (api_name, request_date);


--
-- Name: idx_correction_address_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_correction_address_id ON public.address_correction_log USING btree (address_id);


--
-- Name: idx_housing_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_housing_id ON public.address_mappings USING btree (housing_id);


--
-- Name: idx_unique_addresses_geom; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_unique_addresses_geom ON public.unique_addresses USING gist (geom);


--
-- Name: address_correction_log address_correction_log_address_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.address_correction_log
    ADD CONSTRAINT address_correction_log_address_id_fkey FOREIGN KEY (address_id) REFERENCES public.unique_addresses(address_id);


--
-- Name: address_mappings address_mappings_address_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.address_mappings
    ADD CONSTRAINT address_mappings_address_id_fkey FOREIGN KEY (address_id) REFERENCES public.unique_addresses(address_id);


--
-- Name: address_mappings address_mappings_housing_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.address_mappings
    ADD CONSTRAINT address_mappings_housing_id_fkey FOREIGN KEY (housing_id) REFERENCES public.housing_data(unique_id);


--
-- PostgreSQL database dump complete
--


