# Install Python deps
python3 -m pip install pandas sqlalchemy psycopg2


# Remember to set password as $env:PGPASSWORD = '*********'
# Remember path: .\scripts\run.ps1
mkdir -p out
python3 .\scripts\load_csv.py --host=localhost --username=postgres --dbname=postgres --dataset='.\data\dataset.csv' --tablename="HousingDataRaw"
psql --host=localhost --username=postgres --dbname=postgres -f .\src\cleaning.sql