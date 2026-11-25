from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.db import pool
from app.main import app
from app.modulos import pasarela_simulada
from app.modulos.pedidos import procesar_eventos_pendientes
from escenarios import nuevo_cliente
from escenarios_c3 import estado_pedido, estado_intento, tienda_con_productos
from escenarios_faltantes import contar, nuevo_usuario

client = TestClient(app)


@pytest.fixture(autouse=True)
def simulador_limpio():
    pasarela_simulada.reiniciar_simulador()
    yield
    pasarela_simulada.reiniciar_simulador()


def checkout(darkstore_id, lineas, cliente_id):
    respuesta = client.post(
        "/v1/checkouts",
        headers={"Clave-Operacion": str(uuid4()), "Cliente-Id": str(cliente_id)},
        json={"darkstore_id": str(darkstore_id), "lineas": [{"producto_id": str(p), "cantidad": c} for p, c in lineas]},
    )
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()["pedido"]


def pagar_sin_respuesta(pedido, cliente_id):
    pasarela_simulada._estado_simulador["cobro"] = "NO_RESPONDER"
    respuesta = client.post(
        f"/v1/pedidos/{pedido['pedido_id']}/intentos-pago",
        headers={"Clave-Operacion": str(uuid4()), "Cliente-Id": str(cliente_id)},
        json={"monto": pedido["total_vigente"]},
    )
    assert respuesta.status_code == 202, respuesta.text
    return respuesta.json()["intento_id"]


def aprobar(intento_id):
    respuesta = client.post("/v1/pasarela/notificaciones", json={
        "objeto": "INTENTO", "objeto_id": intento_id, "referencia_externa": f"PSP-{uuid4().hex[:8]}",
        "resultado": "APROBADO", "detalle": {"origen": "webhook"},
    })
    assert respuesta.status_code == 200, respuesta.text
    procesar_eventos_pendientes()


def reembolsos(intento_id):
    with pool.connection() as conn:
        return conn.execute(
            "SELECT monto, motivo, producto_origen_id, fecha_confirmacion IS NOT NULL FROM reembolso "
            "WHERE intento_id = %s ORDER BY fecha_solicitud", (intento_id,),
        ).fetchall()


def total_vigente(pedido_id):
    with pool.connection() as conn:
        return conn.execute("SELECT total_vigente FROM pedido WHERE pedido_id = %s", (pedido_id,)).fetchone()[0]


def faltante_en(darkstore_id, producto_id, contadas):
    picker = nuevo_usuario(darkstore_id, "PICKER")
    assert contar(darkstore_id, producto_id, picker, contadas).status_code == 201


def test_producto_retirado_durante_el_pago_se_reembolsa_al_confirmar():
    tienda, (playera, gorra) = tienda_con_productos(2, unidades=5, precio="100.00")
    cliente = nuevo_cliente()
    pedido = checkout(tienda, [(playera, 2), (gorra, 1)], cliente)
    intento = pagar_sin_respuesta(pedido, cliente)

    faltante_en(tienda, gorra, 0)
    aprobar(intento)

    assert estado_pedido(pedido["pedido_id"]) == "CONFIRMADO"
    assert total_vigente(pedido["pedido_id"]) == Decimal("200.00")
    assert reembolsos(intento) == [(Decimal("100.00"), "PRODUCTO_RETIRADO", gorra, True)]
    assert estado_intento(intento) == "REEMBOLSADO_PARCIAL"


def test_dos_productos_retirados_reembolsan_cada_uno_una_vez():
    tienda, (playera, gorra, calceta) = tienda_con_productos(3, unidades=5, precio="100.00")
    cliente = nuevo_cliente()
    pedido = checkout(tienda, [(playera, 1), (gorra, 2), (calceta, 1)], cliente)
    intento = pagar_sin_respuesta(pedido, cliente)

    faltante_en(tienda, gorra, 0)
    faltante_en(tienda, calceta, 0)
    aprobar(intento)
    aprobar(intento)

    montos = sorted((m, p) for m, _, p, _ in reembolsos(intento))
    assert montos == sorted([(Decimal("200.00"), gorra), (Decimal("100.00"), calceta)])
    assert sum(m for m, _ in montos) == Decimal("400.00") - total_vigente(pedido["pedido_id"])


def test_si_se_retira_antes_de_pagar_no_hay_reembolso():
    tienda, (playera, gorra) = tienda_con_productos(2, unidades=5, precio="100.00")
    cliente = nuevo_cliente()
    pedido = checkout(tienda, [(playera, 1), (gorra, 1)], cliente)
    faltante_en(tienda, gorra, 0)

    pasarela_simulada._estado_simulador["cobro"] = "APROBAR"
    respuesta = client.post(
        f"/v1/pedidos/{pedido['pedido_id']}/intentos-pago",
        headers={"Clave-Operacion": str(uuid4()), "Cliente-Id": str(cliente)},
        json={"monto": "100.00"},
    )
    assert respuesta.status_code == 201, respuesta.text
    procesar_eventos_pendientes()

    assert estado_pedido(pedido["pedido_id"]) == "CONFIRMADO"
    assert reembolsos(respuesta.json()["intento_id"]) == []


def test_si_se_retira_todo_durante_el_pago_se_reembolsa_completo():
    tienda, (gorra,) = tienda_con_productos(1, unidades=5, precio="100.00")
    cliente = nuevo_cliente()
    pedido = checkout(tienda, [(gorra, 3)], cliente)
    intento = pagar_sin_respuesta(pedido, cliente)

    faltante_en(tienda, gorra, 0)
    aprobar(intento)

    assert sum(m for m, *_ in reembolsos(intento)) == Decimal("300.00")
    assert [m for _, m, *_ in reembolsos(intento)] == ["PRODUCTO_RETIRADO"]
    assert estado_intento(intento) == "REEMBOLSADO_TOTAL"
