#! /bin/env bash

# Install Python deps in venv
python -m venv ./.venv
source .venv/bin/activate
python3 -m pip install pandas sqlalchemy psycopg2
