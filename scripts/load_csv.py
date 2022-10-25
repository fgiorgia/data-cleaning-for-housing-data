import pandas as pd
from sqlalchemy import create_engine
import argparse

def main():
  # Initialize parser
  parser = argparse.ArgumentParser()

  # Adding optional argument
  parser.add_argument('--host', required=True)
  parser.add_argument('--port', default=5432)
  parser.add_argument('--username', required=True)
  parser.add_argument('--password', required=True)
  parser.add_argument('--database', required=True)
  parser.add_argument('--dataset', required=True)
  parser.add_argument('--tablename', default='dataset')

  # Read arguments from command line
  args = parser.parse_args()

  
def copy_dataset_to_db():
  # Instantiate sqlachemy.create_engine object
  engine = create_engine(f'postgresql://{args.username}:{args.password}@{args.host}:{args.port}/{args.database}')

  # Create an iterable that reads "chunksize=1000" rows
  # at a time from the CSV file
  for df in pd.read_csv(args.dataset, names='infer', chunksize=1000):
    df.to_sql(
      args.tablename, 
      engine,
      index=False,
      if_exists='append' # if the table already exists, append this data
    )

if __name__ == '__main__':
  main()
  
