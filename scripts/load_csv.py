import pandas as pd
from sqlalchemy import create_engine
import argparse

from scripts.config import get_db_config, DBConfig


def load_csv_to_db(datasetFile: str, table: str, dbConfig: DBConfig):
  # Instantiate sqlachemy.create_engine object
  engine = create_engine(f'postgresql://{dbConfig["username"]}:{dbConfig["password"]}@{dbConfig["hostname"]}:{dbConfig["port"]}/{dbConfig["database"]}')

  # Create an iterable that reads "chunksize=1000" rows
  # at a time from the CSV file
  for df in pd.read_csv(datasetFile, chunksize=1000, encoding="UTF8"):
    print('...')
    df.to_sql(
      table, 
      engine,
      index=False,
      if_exists='append' # if the table already exists, append this data
    )


def main():
  # # Initialize parser
  parser = argparse.ArgumentParser()

  # # Adding optional arguments
  parser.add_argument('--datasetFile', required=True)
  parser.add_argument('--table', default='dataset')

  # Read arguments from command line
  args = parser.parse_args()
  dbConfig = get_db_config()
  
  if dbConfig["password"] is None:
    print("Error: Missing Postgres password, ensure your env is set-up correctly")
    return exit(1)

  print('Copying dataset to database...')
  load_csv_to_db(args.datasetFile, args.table, get_db_config())
  print('Complete!')

  
if __name__ == '__main__':
  main()
