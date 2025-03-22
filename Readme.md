# Data cleaning for housing data

Cleaning the *Nashville Housing Data* dataset.

## Initial setup

You need [Poetry](https://python-poetry.org/docs/#installation) to setup this project.

```sh
poetry install
```

Set your Postgres as `PGPASSWORD` env variable.

On Unix-like envs:

```sh
export PGPASSWORD="replacewithyourpostgrespassword"
```

On Windows:

```powershell
$env:PGPASSWORD="replacewithyourpostgrespassword"
```

You need to install the Levenshtein Postgres exstension before running the cleaning script.

Run the cleaning script

```ps
scripts\run.ps1
```
