from concurrent.futures import ThreadPoolExecutor
import threading
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app.db import pool
from app.main import app
from app.modulos import pasarela_simulada
from app.modulos.reservas import LineaSolicitud, crear_checkout
from escenarios import (
    crear_reserva_vencida,
    estados,
    nuevo_cliente,
    preparar_escenario,
    saldos,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def restaurar_pasarela():
    pasarela_simulada.reiniciar_simulador()
    yield
    pasarela_simulada.reiniciar_simulador()


def _crear_pedido_listo(unidades_stock: int = 5, cantidad_pedida: int = 2):
    darkstore_id, producto_id = preparar_escenario(unidades_stock)
    cliente_id = nuevo_cliente()
    with pool.connection() as conn:
        res = crear_checkout(
            conn,
            cliente_id,
            darkstore_id,
            [LineaSolicitud(producto_id=producto_id, cantidad=cantidad_pedida)],
            uuid4(),
        )
    return (
        darkstore_id,
        producto_id,
        cliente_id,
        res["pedido"]["pedido_id"],
        res["reserva"]["reserva_id"],
    )


def test_pago_aprobado_a_tiempo_confirma_el_pedido():
    darkstore_id, producto_id, cliente_id, pedido_id, reserva_id = _crear_pedido_listo(5, 2)

    resp = client.post(
        f"/v1/pedidos/{pedido_id}/intentos-pago",
        headers={
            "Clave-Operacion": str(uuid4()),
            "Cliente-Id": str(cliente_id),
        },
        json={"monto": "200.00"},
    )
    assert resp.status_code == 201
    datos = resp.json()
    assert datos["estado"] == "APROBADO"
    assert datos["monto"] == "200.00"

    estado_res, motivo_cierre, estado_ped = estados(reserva_id)
    assert estado_res == "COMPROMETIDA"
    assert motivo_cierre == "PAGO_CONFIRMADO"
    assert estado_ped == "CONFIRMADO"

    e, r, d = saldos(darkstore_id, producto_id)
    assert e == 5
    assert r == 0
    assert d == 3

    with pool.connection() as conn:
        fila = conn.execute(
            "SELECT q, estado FROM linea_pedido WHERE pedido_id = %s",
            (pedido_id,),
        ).fetchone()
    assert fila == (2, "ASIGNADA")


def test_pago_rechazado_deja_la_reserva_activa_y_permite_reintentar():
    _, _, cliente_id, pedido_id, reserva_id = _crear_pedido_listo(5, 2)
    pasarela_simulada._estado_simulador["cobro"] = "RECHAZAR"

    resp1 = client.post(
        f"/v1/pedidos/{pedido_id}/intentos-pago",
        headers={"Clave-Operacion": str(uuid4()), "Cliente-Id": str(cliente_id)},
        json={"monto": "200.00"},
    )
    assert resp1.status_code == 201
    assert resp1.json()["estado"] == "RECHAZADO"

    estado_res, _, estado_ped = estados(reserva_id)
    assert estado_res == "ACTIVA"
    assert estado_ped == "PENDIENTE_DE_PAGO"

    # Segundo intento tras rechazo (RT-01)
    pasarela_simulada._estado_simulador["cobro"] = "APROBAR"
    resp2 = client.post(
        f"/v1/pedidos/{pedido_id}/intentos-pago",
        headers={"Clave-Operacion": str(uuid4()), "Cliente-Id": str(cliente_id)},
        json={"monto": "200.00"},
    )
    assert resp2.status_code == 201
    assert resp2.json()["estado"] == "APROBADO"
    assert estados(reserva_id)[2] == "CONFIRMADO"


def test_sin_respuesta_de_la_pasarela_responde_202_en_verificacion():
    _, _, cliente_id, pedido_id, _ = _crear_pedido_listo(5, 2)
    pasarela_simulada._estado_simulador["cobro"] = "NO_RESPONDER"

    resp = client.post(
        f"/v1/pedidos/{pedido_id}/intentos-pago",
        headers={"Clave-Operacion": str(uuid4()), "Cliente-Id": str(cliente_id)},
        json={"monto": "200.00"},
    )
    assert resp.status_code == 202
    assert resp.json()["estado"] == "EN_VERIFICACION"


def test_con_intento_abierto_otro_pago_da_intento_abierto():
    _, _, cliente_id, pedido_id, _ = _crear_pedido_listo(5, 2)
    pasarela_simulada._estado_simulador["cobro"] = "NO_RESPONDER"

    resp1 = client.post(
        f"/v1/pedidos/{pedido_id}/intentos-pago",
        headers={"Clave-Operacion": str(uuid4()), "Cliente-Id": str(cliente_id)},
        json={"monto": "200.00"},
    )
    assert resp1.status_code == 202

    resp2 = client.post(
        f"/v1/pedidos/{pedido_id}/intentos-pago",
        headers={"Clave-Operacion": str(uuid4()), "Cliente-Id": str(cliente_id)},
        json={"monto": "200.00"},
    )
    assert resp2.status_code == 409
    assert resp2.json()["codigo"] == "INTENTO_ABIERTO"


def test_misma_clave_devuelve_la_respuesta_guardada():
    _, _, cliente_id, pedido_id, _ = _crear_pedido_listo(5, 2)
    clave = str(uuid4())

    resp1 = client.post(
        f"/v1/pedidos/{pedido_id}/intentos-pago",
        headers={"Clave-Operacion": clave, "Cliente-Id": str(cliente_id)},
        json={"monto": "200.00"},
    )
    assert resp1.status_code == 201
    assert "Resultado-Previo" not in resp1.headers

    resp2 = client.post(
        f"/v1/pedidos/{pedido_id}/intentos-pago",
        headers={"Clave-Operacion": clave, "Cliente-Id": str(cliente_id)},
        json={"monto": "200.00"},
    )
    assert resp2.status_code == 201
    assert resp2.headers.get("Resultado-Previo") == "true"
    assert resp2.json() == resp1.json()


def test_misma_clave_con_otro_monto_da_conflicto_de_clave():
    _, _, cliente_id, pedido_id, _ = _crear_pedido_listo(5, 2)
    clave = str(uuid4())

    resp1 = client.post(
        f"/v1/pedidos/{pedido_id}/intentos-pago",
        headers={"Clave-Operacion": clave, "Cliente-Id": str(cliente_id)},
        json={"monto": "200.00"},
    )
    assert resp1.status_code == 201

    resp2 = client.post(
        f"/v1/pedidos/{pedido_id}/intentos-pago",
        headers={"Clave-Operacion": clave, "Cliente-Id": str(cliente_id)},
        json={"monto": "100.00"},
    )
    assert resp2.status_code == 409
    assert resp2.json()["codigo"] == "CONFLICTO_DE_CLAVE"


def test_notificacion_repetida_no_cambia_nada():
    _, _, cliente_id, pedido_id, _ = _crear_pedido_listo(5, 2)
    pasarela_simulada._estado_simulador["cobro"] = "NO_RESPONDER"

    resp_pago = client.post(
        f"/v1/pedidos/{pedido_id}/intentos-pago",
        headers={"Clave-Operacion": str(uuid4()), "Cliente-Id": str(cliente_id)},
        json={"monto": "200.00"},
    )
    intento_id = resp_pago.json()["intento_id"]
    payload_notif = {
        "objeto": "INTENTO",
        "objeto_id": intento_id,
        "referencia_externa": "PSP-999001",
        "resultado": "APROBADO",
        "detalle": {"origen": "webhook"},
    }

    r1 = client.post("/v1/pasarela/notificaciones", json=payload_notif)
    assert r1.status_code == 200
    assert r1.json()["estado_aplicado"] == "APROBADO"
    assert "Resultado-Previo" not in r1.headers

    r2 = client.post("/v1/pasarela/notificaciones", json=payload_notif)
    assert r2.status_code == 200
    assert r2.headers.get("Resultado-Previo") == "true"
    assert r2.json() == r1.json()


def test_reserva_vencida_no_permite_iniciar_pago():
    darkstore_id, producto_id = preparar_escenario(5)
    cliente_id = nuevo_cliente()
    pedido_id, _ = crear_reserva_vencida(cliente_id, darkstore_id, producto_id, cantidad=1)

    resp = client.post(
        f"/v1/pedidos/{pedido_id}/intentos-pago",
        headers={"Clave-Operacion": str(uuid4()), "Cliente-Id": str(cliente_id)},
        json={"monto": "100.00"},
    )
    assert resp.status_code == 409
    assert resp.json()["codigo"] == "RESERVA_VENCIDA"


@pytest.mark.parametrize("num_hilos", [2, 8])
def test_carrera_dos_pagos_a_la_vez_solo_uno_inicia(num_hilos):
    _, _, cliente_id, pedido_id, _ = _crear_pedido_listo(5, 2)
    pasarela_simulada._estado_simulador["cobro"] = "NO_RESPONDER"
    barrera = threading.Barrier(num_hilos)

    def _disparar():
        barrera.wait()
        return client.post(
            f"/v1/pedidos/{pedido_id}/intentos-pago",
            headers={"Clave-Operacion": str(uuid4()), "Cliente-Id": str(cliente_id)},
            json={"monto": "200.00"},
        )

    with ThreadPoolExecutor(max_workers=num_hilos) as executor:
        futuros = [executor.submit(_disparar) for _ in range(num_hilos)]
        respuestas = [f.result() for f in futuros]

    exitos = [r for r in respuestas if r.status_code == 202]
    conflictos = [r for r in respuestas if r.status_code == 409]

    assert len(exitos) == 1
    assert len(conflictos) == num_hilos - 1
    for c in conflictos:
        assert c.json()["codigo"] == "INTENTO_ABIERTO"