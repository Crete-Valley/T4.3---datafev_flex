import os
from dotenv import load_dotenv
from psycopg_pool import ConnectionPool

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")


pool = ConnectionPool(conninfo=(DATABASE_URL), open=True)

def get_conn():
    return pool.connection()
