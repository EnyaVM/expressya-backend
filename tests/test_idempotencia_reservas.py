from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.db import pool
from app.main import app
from app.modulos.reservas import LineaSolicitud, crear_checkout, liberar_reserva
from escenarios import crear_reserva_vencida, estados, movimientos_de, nuevo_cliente, preparar_escenario, saldos


http = TestClient(app)


def checkout(unidades=5, cantidad=1):
    tienda, producto = preparar_escenario(unidades)
    cabeceras = {"Cliente-Id": str(nuevo_cliente()), "Clave-Operacion": str(uuid4())}
    cuerpo = {"darkstore_id": str(tienda), "lineas": [{"producto_id": str(producto), "cantidad": cantidad}]}
    return cabeceras, cuerpo, tienda, producto


def test_repetir_checkout_despues_de_liberar_conserva_la_respuesta_original():
    cabeceras, cuerpo, tienda, producto = checkout()
    primera = http.post("/v1/checkouts", headers=cabeceras, json=cuerpo)
    assert primera.status_code == 201
    reserva = primera.json()["reserva"]["reserva_id"]
    cierre = http.post(f"/v1/reservas/{reserva}/cierre", json={"motivo": "CANCELACION_CLIENTE"},
                       headers={**cabeceras, "Clave-Operacion": str(uuid4())})
    assert cierre.status_code == 200
    repetida = http.post("/v1/checkouts", headers=cabeceras, json=cuerpo)
    assert repetida.status_code == 201
    assert repetida.json() == primera.json()
    assert repetida.headers["Resultado-Previo"] == "true"
    assert saldos(tienda, producto) == (5, 0, 5)


@pytest.mark.parametrize("cambio", ["cantidad", "cliente", "operacion"])
def test_reusar_clave_para_otra_solicitud_da_conflicto(cambio):
    cabeceras, cuerpo, tienda, producto = checkout()
    primera = http.post("/v1/checkouts", headers=cabeceras, json=cuerpo)
    assert primera.status_code == 201
    ruta = "/v1/checkouts"
    if cambio == "cantidad":
        cuerpo["lineas"][0]["cantidad"] = 2
    elif cambio == "cliente":
        cabeceras["Cliente-Id"] = str(nuevo_cliente())
    else:
        ruta = f"/v1/reservas/{primera.json()['reserva']['reserva_id']}/cierre"
        cuerpo = {"motivo": "CANCELACION_CLIENTE"}
    otra = http.post(ruta, headers=cabeceras, json=cuerpo)
    assert otra.status_code == 409
    assert otra.json()["codigo"] == "CONFLICTO_DE_CLAVE"
    assert saldos(tienda, producto) == (5, 1, 4)


@pytest.mark.parametrize("unidades,esperado,resultado", [(2, 201, "APLICADA"), (1, 409, "RECHAZADA")])
def test_carrera_misma_clave_un_registro_y_un_solo_efecto(unidades, esperado, resultado):
    cabeceras, cuerpo, tienda, producto = checkout(unidades, cantidad=2)
    barrera = Barrier(2)

    def enviar():
        barrera.wait(timeout=10)
        return http.post("/v1/checkouts", headers=cabeceras, json=cuerpo)

    with ThreadPoolExecutor(max_workers=2) as ejecutor:
        futuros = [ejecutor.submit(enviar) for _ in range(2)]
        a, b = [futuro.result(timeout=20) for futuro in futuros]
    assert a.status_code == b.status_code == esperado
    assert a.json() == b.json()
    assert {a.headers.get("Resultado-Previo"), b.headers.get("Resultado-Previo")} == {None, "true"}
    with pool.connection() as conn:
        registros = conn.execute("SELECT resultado FROM registro_operacion WHERE clave_operacion = %s",
                                 (cabeceras["Clave-Operacion"],)).fetchall()
    assert registros == [(resultado,)]
    reservadas = 2 if esperado == 201 else 0
    assert saldos(tienda, producto) == (unidades, reservadas, unidades - reservadas)


def test_rechazo_revierte_la_expiracion_parcial_y_conserva_el_error():
    cabeceras, cuerpo, tienda, producto = checkout(unidades=2, cantidad=3)
    _, vencida = crear_reserva_vencida(cabeceras["Cliente-Id"], tienda, producto)
    previos = movimientos_de(vencida)
    primera = http.post("/v1/checkouts", headers=cabeceras, json=cuerpo)
    assert primera.status_code == 409
    assert primera.json()["codigo"] == "SIN_DISPONIBLE"
    assert estados(vencida) == ("ACTIVA", None, "PENDIENTE_DE_PAGO")
    assert movimientos_de(vencida) == previos
    repetida = http.post("/v1/checkouts", headers=cabeceras, json=cuerpo)
    assert repetida.json() == primera.json()
    assert repetida.headers["Resultado-Previo"] == "true"


def test_clave_anterior_con_movimiento_sin_respuesta_no_crea_otro_checkout():
    cabeceras, cuerpo, tienda, producto = checkout()
    cliente, clave = UUID(cabeceras["Cliente-Id"]), UUID(cabeceras["Clave-Operacion"])
    with pool.connection() as conn:
        original = crear_checkout(conn, cliente, tienda, [LineaSolicitud(producto_id=producto, cantidad=1)], clave)
        liberar_reserva(conn, cliente, original["reserva"]["reserva_id"], "CANCELACION_CLIENTE")
    repetida = http.post("/v1/checkouts", headers=cabeceras, json=cuerpo)
    assert repetida.status_code == 409
    assert repetida.json()["codigo"] == "CONFLICTO_DE_CLAVE"
    assert saldos(tienda, producto) == (5, 0, 5)
