# Install Python deps in venv
python -m venv ./.venv
.\.venv\Scripts\Activate.ps1
python3 -m pip install pandas sqlalchemy psycopg2
