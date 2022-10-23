DO $$
BEGIN 
	RAISE NOTICE 'Cleaning Nashville Data...';
END $$;

-- Create DB table
CREATE TABLE persons (
  id SERIAL,
  first_name VARCHAR(50),
  last_name VARCHAR(50),
  dob DATE,
  email VARCHAR(255),
  PRIMARY KEY (id)
);

\! pwd

-- Load dataset into our table
\copy persons(first_name, last_name, dob, email) FROM './data/dataset.csv' DELIMITER ',' CSV HEADER;

-- Check that loading worked
SELECT * FROM persons;

-- Save table back into dataset
\copy persons(first_name, last_name, dob, email) TO './out/dataset.csv' DELIMITER ',' CSV HEADER;


DO $$
BEGIN 
	RAISE NOTICE 'Cleaning Nashville Data complete!';
END $$;
