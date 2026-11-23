from uuid import uuid4

from app.db import pool
from app.modulos.avisos import registrar_aviso
from escenarios import nuevo_cliente


def _avisos(origen_id):
    with pool.connection() as conn:
        return conn.execute(
            "SELECT destinatario_tipo, cliente_id, tipo FROM aviso WHERE origen_id = %s",
            (origen_id,),
        ).fetchall()


def test_un_origen_genera_un_solo_aviso_aunque_se_registre_dos_veces():
    cliente_id = nuevo_cliente()
    origen_id = uuid4()
    with pool.connection() as conn:
        primera = registrar_aviso(conn, "PEDIDO_CANCELADO", "PEDIDO", origen_id, cliente_id=cliente_id)
        segunda = registrar_aviso(conn, "PEDIDO_CANCELADO", "PEDIDO", origen_id, cliente_id=cliente_id)

    assert primera == segunda
    assert _avisos(origen_id) == [("CLIENTE", cliente_id, "PEDIDO_CANCELADO")]


def test_aviso_sin_cliente_va_al_responsable():
    origen_id = uuid4()
    with pool.connection() as conn:
        registrar_aviso(conn, "ALERTA", "DISCREPANCIA", origen_id)

    assert _avisos(origen_id) == [("RESPONSABLE", None, "ALERTA")]
