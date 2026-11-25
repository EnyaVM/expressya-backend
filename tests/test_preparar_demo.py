import os

import psycopg

from scripts.preparar_demo import actualizar_base, sincronizar_catalogo

SUDADERA = "55555555-5555-5555-5555-555555555551"


def precio_de(conn, producto_id):
    return str(conn.execute(
        "SELECT precio_unitario FROM expressya.producto WHERE producto_id = %s", (producto_id,)
    ).fetchone()[0])


def test_la_demo_toma_los_precios_editados_en_el_catalogo():
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        original = precio_de(conn, SUDADERA)
        conn.execute("UPDATE expressya.producto SET precio_unitario = 1 WHERE producto_id = %s", (SUDADERA,))
        sincronizar_catalogo(conn)
        assert precio_de(conn, SUDADERA) == original


def test_sincronizar_dos_veces_deja_el_mismo_catalogo():
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        sincronizar_catalogo(conn)
        antes = conn.execute("SELECT count(*) FROM expressya.producto").fetchone()[0]
        sincronizar_catalogo(conn)
        assert conn.execute("SELECT count(*) FROM expressya.producto").fetchone()[0] == antes


def test_una_base_al_dia_no_vuelve_a_cargar_archivos():
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        actualizar_base(conn)
        assert actualizar_base(conn) == []


def test_un_archivo_nuevo_se_aplica_una_sola_vez(tmp_path, monkeypatch):
    (tmp_path / "db").mkdir()
    (tmp_path / "db" / "99_prueba_nueva.sql").write_text("CREATE TABLE expressya.prueba_nueva (x int)")
    monkeypatch.setattr("scripts.preparar_demo.RAIZ", tmp_path)
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        try:
            assert actualizar_base(conn) == ["99_prueba_nueva.sql"]
            assert actualizar_base(conn) == []
        finally:
            conn.execute("DROP TABLE IF EXISTS expressya.prueba_nueva")
            conn.execute("DELETE FROM expressya.archivo_cargado WHERE nombre = '99_prueba_nueva.sql'")


def test_una_demo_creada_antes_del_registro_no_repite_lo_ya_cargado():
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute("DROP TABLE expressya.archivo_cargado")
        assert actualizar_base(conn) == []
