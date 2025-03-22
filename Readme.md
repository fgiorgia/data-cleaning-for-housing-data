# Data cleaning for housing data

Cleaning the *Nashville Housing Data* dataset.

Initial setup

```ps
scripts\setup.ps1
```

Set your Postgres password with

```ps
$env:PGPASSWORD="replacewithyourpostgrespassword"
```

You need to install the Levenshtein Postgres exstension before running the cleaning script.

Run the cleaning script

```ps
scripts\run.ps1
```
