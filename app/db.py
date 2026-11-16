import os

from dotenv import load_dotenv
from psycopg_pool import ConnectionPool

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/expressya_db")

pool = ConnectionPool(
    DATABASE_URL,
    min_size=2,
    max_size=10,
    kwargs={"options": "-c search_path=expressya,public"},
    open=True,
)
