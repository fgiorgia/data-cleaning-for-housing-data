import sys
from scripts.config import get_db_config
import subprocess
import os
import shlex
import signal

def main():
    dbConfig = get_db_config()
    password = dbConfig["password"]
    if password is None:
        print("Error: Missing Postgres password, ensure your env is set-up correctly")
        return exit(1)
    
    psql_argv = sys.argv[1:]
    base_command = f"psql -P pager=off --host={dbConfig['hostname']} --username={dbConfig['username']} --dbname={dbConfig['database']} --port={dbConfig['port']}"
    split_command = shlex.split(base_command) + psql_argv
    custom_env = os.environ.copy()
    custom_env["PGPASSWORD"] = password
  
    subprocess.run(
        split_command, 
        stdout=None,
        stderr=None,
        check=True,
        env=custom_env,
      )

if __name__ == "__main__":
    main()
