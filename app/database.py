import os
import psycopg
from psycopg_pool import ConnectionPool
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/expressya_db")

# Pool de conexiones para alto rendimiento bajo concurrencia
pool = ConnectionPool(conninfo=DATABASE_URL, min_size=2, max_size=10)

def get_db_connection():
    """Obtiene una conexión del pool para las transacciones ACID."""
    return pool.connection()
