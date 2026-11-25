from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.db import pool
from app.main import app
from app.modulos import pasarela_simulada
from app.modulos.pagos import enviar_reembolso, solicitar_reembolso
from escenarios_c3 import pedido_pagado, tienda_con_productos
from escenarios_faltantes import nuevo_usuario

client = TestClient(app)


@pytest.fixture(autouse=True)
def pasarela_limpia():
    pasarela_simulada.reiniciar_simulador()
    yield
    pasarela_simulada.reiniciar_simulador()


def intento_en_verificacion():
    tienda, (producto,) = tienda_con_productos(1)
    from escenarios import nuevo_cliente

    cliente = nuevo_cliente()
    pedido = client.post("/v1/checkouts", headers={"Clave-Operacion": str(uuid4()), "Cliente-Id": str(cliente)},
                         json={"darkstore_id": str(tienda), "lineas": [{"producto_id": str(producto), "cantidad": 1}]}).json()["pedido"]
    pasarela_simulada._estado_simulador["cobro"] = "NO_RESPONDER"
    intento = client.post(f"/v1/pedidos/{pedido['pedido_id']}/intentos-pago",
                          headers={"Clave-Operacion": str(uuid4()), "Cliente-Id": str(cliente)},
                          json={"monto": pedido["total_vigente"]}).json()["intento_id"]
    return tienda, intento


def reembolso_en(estado_pasarela):
    tienda, (producto,) = tienda_con_productos(1)
    _, _, intento = pedido_pagado(tienda, [(producto, 1)])
    with pool.connection() as conn, conn.transaction():
        reembolso = solicitar_reembolso(conn, intento, Decimal("100.00"), "PRUEBA")
    pasarela_simulada._estado_simulador["reembolso"] = estado_pasarela
    enviar_reembolso(reembolso)
    return tienda, reembolso


def verificar_intento(intento, usuario):
    return client.post(f"/v1/intentos-pago/{intento}/verificacion",
                       headers={"Clave-Operacion": str(uuid4()), "Usuario-Id": str(usuario)},
                       json={"resultado": "APROBADO", "evidencia_fuente": "consulta", "evidencia": {"ref": "x"}})


def test_un_responsable_de_otra_tienda_no_aprueba_el_pago():
    tienda, intento = intento_en_verificacion()
    ajeno = nuevo_usuario(tienda_con_productos(1)[0], "RESPONSABLE")
    propio = nuevo_usuario(tienda, "RESPONSABLE")

    rechazo = verificar_intento(intento, ajeno)

    assert rechazo.status_code == 403 and rechazo.json()["codigo"] == "SIN_PERMISO"
    assert verificar_intento(intento, propio).json()["estado"] == "APROBADO"


def test_un_responsable_de_otra_tienda_no_resuelve_un_reembolso():
    tienda, reembolso = reembolso_en("NO_RESPONDER")
    ajeno = nuevo_usuario(tienda_con_productos(1)[0], "RESPONSABLE")
    cuerpo = {"resultado": "CONFIRMADO", "evidencia": {"ref": "x"}}
    url = f"/v1/reembolsos/{reembolso}/verificacion"

    assert client.post(url, headers={"Clave-Operacion": str(uuid4()), "Usuario-Id": str(ajeno)}, json=cuerpo).status_code == 403
    propio = nuevo_usuario(tienda, "RESPONSABLE")
    assert client.post(url, headers={"Clave-Operacion": str(uuid4()), "Usuario-Id": str(propio)}, json=cuerpo).status_code == 200


def test_un_responsable_de_otra_tienda_no_reintenta_un_reembolso():
    tienda, reembolso = reembolso_en("FALLAR")
    ajeno = nuevo_usuario(tienda_con_productos(1)[0], "RESPONSABLE")
    url = f"/v1/reembolsos/{reembolso}/reintentos"

    assert client.post(url, headers={"Clave-Operacion": str(uuid4()), "Usuario-Id": str(ajeno)}).status_code == 403
    pasarela_simulada._estado_simulador["reembolso"] = "CONFIRMAR"
    propio = nuevo_usuario(tienda, "RESPONSABLE")
    assert client.post(url, headers={"Clave-Operacion": str(uuid4()), "Usuario-Id": str(propio)}).status_code == 202
