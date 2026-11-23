from decimal import Decimal

import pytest

from app.db import pool
from app.modulos import pasarela_simulada
from escenarios_c3 import (
    aplicar_faltante,
    cancelar,
    crear_resolucion_faltante,
    estado_intento,
    estado_pedido,
    lineas_de,
    pedido_pagado,
    reembolsos_de,
    saldos_de,
    tienda_con_productos,
)


@pytest.fixture(autouse=True)
def restaurar_pasarela():
    pasarela_simulada.reiniciar_simulador()
    yield
    pasarela_simulada.reiniciar_simulador()


def test_flujo_completo_checkout_pago_faltante_cancelacion_y_reembolso():
    darkstore_id, productos = tienda_con_productos(2, unidades=5, precio="250.00")
    pedido_id, cliente_id, intento_id = pedido_pagado(darkstore_id, [(productos[0], 2), (productos[1], 1)])
    assert saldos_de(darkstore_id, productos[0]) == (5, 0, 2, 3)

    linea_id = lineas_de(pedido_id)[productos[0]]["linea_id"]
    aplicar_faltante(crear_resolucion_faltante(darkstore_id, productos[0], linea_id, canceladas=1), linea_id)
    assert estado_pedido(pedido_id) == "CONFIRMADO"
    assert estado_intento(intento_id) == "REEMBOLSADO_PARCIAL"

    respuesta = cancelar(pedido_id, cliente_id)
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["monto_reembolso"] == "500.00"

    assert estado_pedido(pedido_id) == "CANCELADO"
    assert estado_intento(intento_id) == "REEMBOLSADO_TOTAL"
    assert sum(r[1] for r in reembolsos_de(intento_id)) == Decimal("750.00")
    for producto_id in productos:
        e, r, c, d = saldos_de(darkstore_id, producto_id)
        assert (r, c, d) == (0, 0, e)

    with pool.connection() as conn:
        historial = conn.execute(
            """
            SELECT m.tipo, ms.saldo, ms.cantidad, m.usuario_id IS NOT NULL, m.motivo
            FROM movimiento_inventario m
            JOIN movimiento_saldo ms USING (movimiento_id)
            JOIN linea_pedido lp ON lp.reserva_id = m.referencia_reserva_id AND lp.producto_id = m.producto_id
            WHERE lp.linea_id = %s
            ORDER BY m.fecha
            """,
            (linea_id,),
        ).fetchall()
        avisos = conn.execute(
            "SELECT tipo FROM aviso WHERE pedido_id = %s ORDER BY fecha",
            (pedido_id,),
        ).fetchall()

    tipos = [fila[0] for fila in historial]
    assert "COMPROMISO" in tipos
    cancelaciones = [fila for fila in historial if fila[0] == "CANCELACION_LINEA"]
    assert [(f[1], f[2]) for f in cancelaciones] == [("C", -1), ("C", -1)]
    assert cancelaciones[0][3] is True
    assert all(f[4] for f in cancelaciones)
    assert [a[0] for a in avisos] == ["CANCELACION_PARCIAL", "PEDIDO_CANCELADO"]
