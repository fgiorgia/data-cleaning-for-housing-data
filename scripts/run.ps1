# Set password in PowerShell with: $env:PGPASSWORD = '*********'
# Path: .\scripts\run.ps1

mkdir -p out
.\.venv\Scripts\Activate.ps1
python3 .\scripts\remove_bom.py --input=.\data\dataset.csv --output='.\data\dataset_no_bom.csv'
python3 .\scripts\load_csv.py --host=localhost --username=postgres --dbname=postgres --dataset='.\data\dataset_no_bom.csv' --tablename="HousingDataRaw"
psql --host=localhost --username=postgres --dbname=postgres -f .\src\cleaning.sql
