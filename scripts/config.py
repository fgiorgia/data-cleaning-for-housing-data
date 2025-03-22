import os
from typing import TypedDict
from dotenv import load_dotenv

load_dotenv()

class ConfigInterface(TypedDict):
    hostname: str
    port: str
    database: str
    username: str
    password: str | None

def get_env_config() -> ConfigInterface:
  #  --host=localhost --username=postgres --dbname=postgres --port=5433
  hostname = os.environ.get("DB_HOSTNAME", "localhost")
  port = os.environ.get("DB_PORT", "5432")
  username = os.environ.get("DB_USERNAME", "postgres")
  database = os.environ.get("DB_DATABASE", "postgres")
  password = os.environ.get("DB_PASSWORD", None)

  return {
    "hostname": hostname,
    "port": port,
    "database": database,
    "username": username,
    "password": password
  }

def create_pgpass() -> None:
   # hostname:port:database:username:password
  config = get_env_config()
  pgpass_content = f"{config['hostname']}:{config['port']}:{config['database']}:{config['username']}:{config['password']}\n"
  with open("./.pgpass", "w") as file:
    file.write(pgpass_content)
