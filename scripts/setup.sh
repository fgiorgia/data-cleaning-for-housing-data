#! /bin/env bash

# Install Python deps in venv
python3 -m virtualenv --system-site-packages -p python3 .venv
source .venv/bin/activate
python3 -m pip install pandas sqlalchemy psycopg2
