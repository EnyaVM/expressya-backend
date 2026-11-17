import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ / ".env")
URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/expressya_db")


def main():
    archivos = sorted((RAIZ / "db").glob("*.sql"))
    with psycopg.connect(URL, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS expressya CASCADE")
        for archivo in archivos:
            print(f"Cargando {archivo.name}")
            conn.execute(archivo.read_text(encoding="utf-8"))
    print(f"Base reconstruida con {len(archivos)} archivos")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {error}")
        sys.exit(1)