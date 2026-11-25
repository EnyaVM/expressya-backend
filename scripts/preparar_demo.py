"""Prepara una base separada para la demo sin borrar datos existentes."""
import os
from pathlib import Path
import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo

RAIZ = Path(__file__).resolve().parent.parent
DEFAULT_URL = 'postgresql://postgres:postgres@localhost:5432/expressya_demo_20261123'


def preparar():
    url = os.environ.get('DATABASE_URL', DEFAULT_URL)
    nombre = conninfo_to_dict(url).get('dbname', '')
    if not nombre.startswith('expressya_demo'):
        raise RuntimeError('El lanzador de demo exige una base cuyo nombre empiece por expressya_demo.')
    with psycopg.connect(make_conninfo(url, dbname='postgres'), autocommit=True) as conn:
        if not conn.execute('SELECT 1 FROM pg_database WHERE datname=%s', (nombre,)).fetchone():
            conn.execute(psycopg.sql.SQL('CREATE DATABASE {}').format(psycopg.sql.Identifier(nombre)))
    with psycopg.connect(url, autocommit=True) as conn:
        if not conn.execute("SELECT to_regclass('expressya.pedido')").fetchone()[0]:
            for archivo in sorted((RAIZ / 'db').glob('*.sql')):
                conn.execute(archivo.read_text())
        elif not conn.execute("SELECT 1 FROM information_schema.columns WHERE table_schema='expressya' AND table_name='intento_pago' AND column_name='clave_operacion'").fetchone():
            conn.execute((RAIZ / 'db/03_clave_intento_pago.sql').read_text())
    print('Base de demo lista. Se conservaron los pedidos y movimientos existentes.')


if __name__ == '__main__':
    preparar()
