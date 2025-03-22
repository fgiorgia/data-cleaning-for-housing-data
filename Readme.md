# Data cleaning for housing data

Cleaning the *Nashville Housing Data* dataset.

## Initial setup

### Postgres

A postgres instance is expected to be available on your machine. You can download it here: <https://www.postgresql.org/download/>.
You also need to install the Levenshtein Postgres extension.

### Python

You need Poetry to setup this project. You can install it here: <https://python-poetry.org/docs/#installation>.

Then run the following to install the Poe plugin as well as the project dependencies.

```sh
poetry self add 'poethepoet[poetry_plugin]'
poetry install
```

## Running the pipeline

Set your Postgres password as the `PGPASSWORD` env variable.

On Unix-like envs:

```sh
export PGPASSWORD="replacewithyourpostgrespassword"
```

On Windows:

```powershell
$env:PGPASSWORD="replacewithyourpostgrespassword"
```

Run the cleaning pipeline

```sh
 poetry poe data-cleaning-pipeline
```
