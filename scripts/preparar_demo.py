"""Prepara una base separada para la demo sin borrar datos existentes."""
import os
from pathlib import Path
import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo

RAIZ = Path(__file__).resolve().parent.parent
DEFAULT_URL = 'postgresql://postgres:postgres@localhost:5432/expressya_demo_20261123'
LEGADO = ('01_modelo_fisico_expressya.sql', '02_precio_producto.sql', '90_datos_prueba.sql',
          '91_datos_c1.sql', '92_datos_c2.sql')


def archivos():
    return sorted((RAIZ / 'db').glob('*.sql'))


def es_catalogo(archivo):
    return '_catalogo_' in archivo.name


def sincronizar_catalogo(conn):
    for archivo in filter(es_catalogo, archivos()):
        conn.execute(archivo.read_text())


def crear_registro(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS expressya.archivo_cargado '
                 '(nombre text PRIMARY KEY, cargado_en timestamptz NOT NULL DEFAULT now())')


def marcar(conn, nombres):
    for nombre in nombres:
        conn.execute('INSERT INTO expressya.archivo_cargado (nombre) VALUES (%s) ON CONFLICT DO NOTHING', (nombre,))


def existe(conn, consulta):
    return conn.execute(consulta).fetchone() is not None


def legado_detectado(conn):
    nombres = list(LEGADO)
    if existe(conn, "SELECT 1 FROM information_schema.columns WHERE table_schema='expressya' "
                    "AND table_name='intento_pago' AND column_name='clave_operacion'"):
        nombres.append('03_clave_intento_pago.sql')
    if existe(conn, "SELECT 1 FROM expressya.usuario_operativo "
                    "WHERE usuario_id = '88888888-8888-8888-8888-888888888888'"):
        nombres.append('93_datos_c3.sql')
    return nombres


def cargados(conn):
    return {fila[0] for fila in conn.execute('SELECT nombre FROM expressya.archivo_cargado')}


def actualizar_base(conn):
    crear_registro(conn)
    if not cargados(conn):
        marcar(conn, legado_detectado(conn))
    ya = cargados(conn)
    pendientes = [a for a in archivos() if a.name not in ya and not es_catalogo(a)]
    for archivo in pendientes:
        conn.execute(archivo.read_text())
        marcar(conn, [archivo.name])
    sincronizar_catalogo(conn)
    return [a.name for a in pendientes]


def cargar_todo(conn):
    for archivo in archivos():
        conn.execute(archivo.read_text())
    crear_registro(conn)
    marcar(conn, [a.name for a in archivos()])


def preparar():
    url = os.environ.get('DATABASE_URL', DEFAULT_URL)
    nombre = conninfo_to_dict(url).get('dbname', '')
    if not nombre.startswith('expressya_demo'):
        raise RuntimeError('El lanzador de demo exige una base cuyo nombre empiece por expressya_demo.')
    with psycopg.connect(make_conninfo(url, dbname='postgres'), autocommit=True) as conn:
        if not conn.execute('SELECT 1 FROM pg_database WHERE datname=%s', (nombre,)).fetchone():
            conn.execute(psycopg.sql.SQL('CREATE DATABASE {}').format(psycopg.sql.Identifier(nombre)))
    with psycopg.connect(url, autocommit=True) as conn:
        if conn.execute("SELECT to_regclass('expressya.pedido')").fetchone()[0]:
            nuevos = actualizar_base(conn)
        else:
            cargar_todo(conn)
            nuevos = []
    print(f'Base de demo lista. Catálogo y precios al día; archivos nuevos aplicados: {nuevos or "ninguno"}. '
          'Se conservaron los pedidos y movimientos existentes.')


if __name__ == '__main__':
    preparar()
