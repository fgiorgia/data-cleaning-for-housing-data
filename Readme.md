# Data cleaning for housing data

Cleaning the *Nashville Housing Data* dataset.

## Initial setup

You need [Poetry](https://python-poetry.org/docs/#installation) to setup this project, as well as the poe plugin.

```sh
poetry install
poetry self add 'poethepoet[poetry_plugin]'
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

Run the cleaning pipeline

```sh
 poetry poe data-cleaning-pipeline
```
