import pandas as pd
from sqlalchemy import create_engine
import argparse

def main():
  # Initialize parser
  parser = argparse.ArgumentParser()

  # Adding optional argument
  # --host=localhost --username=postgres --dbname=postgres -f
  parser.add_argument('-h', '--host')
  parser.add_argument('-u', '--username')
  parser.add_argument('-d', '--dbname')
  parser.add_argument('-f', '--file')

  # Read arguments from command line
  args = parser.parse_args()

  if args.Output:
      print("Displaying Output as: % s" % args.Output)
  
def copy_dataset_to_db():
  # Instantiate sqlachemy.create_engine object
  engine = create_engine('postgresql://postgres:my_password@localhost:5432/iris')

  # Create an iterable that reads "chunksize=1000" rows
  # at a time from the CSV file
  for df in pd.read_csv("iris.csv",names='infer',chunksize=1000):
    df.to_sql(
      'HousingDataRaw', 
      engine,
      index=False,
      if_exists='append' # if the table already exists, append this data
    )

if __name__ == '__main__':
  main()
  
