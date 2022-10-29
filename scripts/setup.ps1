# Install Python deps in venv
python3 -m virtualenv --system-site-packages -p python3 .venv
.\.venv\Scripts\Activate.ps1
python3 -m pip install pandas sqlalchemy psycopg2
