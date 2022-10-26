import pandas as pd
from sqlalchemy import create_engine
import argparse
import os

def main():
  # Initialize parser
  parser = argparse.ArgumentParser()

  # Adding optional argument
  parser.add_argument('--host', required=True)
  parser.add_argument('--port', default=5432)
  parser.add_argument('--username', required=True)
  parser.add_argument('--dbname', required=True)
  parser.add_argument('--dataset', required=True)
  parser.add_argument('--tablename', default='dataset')

  # Read arguments from command line
  args = parser.parse_args()
  
  copy_dataset_to_db(args)

  
def copy_dataset_to_db(args):
  # Instantiate sqlachemy.create_engine object
  engine = create_engine(f'postgresql://{args.username}:{os.environ["PGPASSWORD"]}@{args.host}:{args.port}/{args.dbname}')

  # Create an iterable that reads "chunksize=1000" rows
  # at a time from the CSV file
  print('Copying dataset to database...')
  for df in pd.read_csv(args.dataset, sep=None, chunksize=1000, encoding="UTF8"):
    print('...')
    df.to_sql(
      args.tablename, 
      engine,
      index=False,
      if_exists='append' # if the table already exists, append this data
    )
  print('Complete!')

  
if __name__ == '__main__':
  main()
