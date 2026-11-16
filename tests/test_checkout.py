import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.db import pool
from app.errores import ErrorDeNegocio
from app.main import app
from app.modulos.reservas import LineaSolicitud, crear_checkout, verificar_disponibilidad
from escenarios import nuevo_cliente, preparar_escenario, saldos

NORTE = UUID("11111111-1111-1111-1111-111111111111")
PLAYERA = UUID("22222222-2222-2222-2222-222222222222")
JEANS = UUID("33333333-3333-3333-3333-333333333333")
CERO = UUID("00000000-0000-0000-0000-000000000000")

cliente_http = TestClient(app)


def verificar(lineas):
    with pool.connection() as conn:
        return verificar_disponibilidad(conn, NORTE, lineas)


def codigo_del_error(lineas):
    with pytest.raises(ErrorDeNegocio) as error:
        verificar(lineas)
    return error.value.codigo


def test_hay_suficiente_y_devuelve_el_precio():
    precios = verificar([LineaSolicitud(producto_id=PLAYERA, cantidad=3)])
    assert str(precios[PLAYERA]) == "199.00"


def test_pedir_mas_de_lo_disponible_da_sin_disponible():
    assert codigo_del_error([LineaSolicitud(producto_id=PLAYERA, cantidad=4)]) == "SIN_DISPONIBLE"


def test_producto_agotado_da_sin_disponible():
    assert codigo_del_error([LineaSolicitud(producto_id=JEANS, cantidad=1)]) == "SIN_DISPONIBLE"


def test_producto_inexistente_da_no_encontrado():
    assert codigo_del_error([LineaSolicitud(producto_id=CERO, cantidad=1)]) == "NO_ENCONTRADO"


def test_producto_repetido_da_solicitud_invalida():
    lineas = [LineaSolicitud(producto_id=PLAYERA, cantidad=1), LineaSolicitud(producto_id=PLAYERA, cantidad=1)]
    assert codigo_del_error(lineas) == "SOLICITUD_INVALIDA"


def test_crear_checkout_reserva_y_descuenta_disponibilidad():
    darkstore_id, producto_id = preparar_escenario(5)
    with pool.connection() as conn:
        respuesta = crear_checkout(
            conn, nuevo_cliente(), darkstore_id, [LineaSolicitud(producto_id=producto_id, cantidad=2)], uuid4()
        )
    assert respuesta["pedido"]["estado"] == "PENDIENTE_DE_PAGO"
    assert respuesta["pedido"]["total_vigente"] == "200.00"
    assert respuesta["reserva"]["estado"] == "ACTIVA"
    assert saldos(darkstore_id, producto_id) == (5, 2, 3)


def test_la_reserva_vence_exactamente_a_los_300_segundos():
    darkstore_id, producto_id = preparar_escenario(1)
    with pool.connection() as conn:
        respuesta = crear_checkout(
            conn, nuevo_cliente(), darkstore_id, [LineaSolicitud(producto_id=producto_id, cantidad=1)], uuid4()
        )
        diferencia = conn.execute(
            "SELECT t_vence - t_creacion FROM reserva WHERE reserva_id = %s",
            (respuesta["reserva"]["reserva_id"],),
        ).fetchone()[0]
    assert diferencia.total_seconds() == 300


def test_la_reserva_deja_su_movimiento_auditable():
    darkstore_id, producto_id = preparar_escenario(4)
    with pool.connection() as conn:
        respuesta = crear_checkout(
            conn, nuevo_cliente(), darkstore_id, [LineaSolicitud(producto_id=producto_id, cantidad=3)], uuid4()
        )
    with pool.connection() as conn:
        movimiento = conn.execute(
            "SELECT m.tipo, ms.saldo, ms.cantidad FROM movimiento_inventario m "
            "JOIN movimiento_saldo ms USING (movimiento_id) WHERE m.referencia_reserva_id = %s",
            (respuesta["reserva"]["reserva_id"],),
        ).fetchone()
    assert movimiento == ("RESERVA", "R", 3)


@pytest.mark.parametrize("clientes", [2, 8])
def test_la_ultima_unidad_la_gana_exactamente_un_cliente(clientes):
    darkstore_id, producto_id = preparar_escenario(1)
    salida = threading.Barrier(clientes)

    def intentar(cliente):
        salida.wait()
        try:
            with pool.connection() as conn:
                crear_checkout(
                    conn, cliente, darkstore_id, [LineaSolicitud(producto_id=producto_id, cantidad=1)], uuid4()
                )
            return "GANO"
        except ErrorDeNegocio as error:
            return error.codigo

    with ThreadPoolExecutor(max_workers=clientes) as ejecutor:
        resultados = list(ejecutor.map(intentar, [nuevo_cliente() for _ in range(clientes)]))

    assert resultados.count("GANO") == 1
    assert resultados.count("SIN_DISPONIBLE") == clientes - 1
    assert saldos(darkstore_id, producto_id) == (1, 1, 0)


def test_post_checkouts_responde_201_y_luego_409_sin_disponible():
    darkstore_id, producto_id = preparar_escenario(1)
    cuerpo = {"darkstore_id": str(darkstore_id), "lineas": [{"producto_id": str(producto_id), "cantidad": 1}]}

    def cabeceras():
        return {"Clave-Operacion": str(uuid4()), "Cliente-Id": str(nuevo_cliente())}

    primera = cliente_http.post("/v1/checkouts", json=cuerpo, headers=cabeceras())
    segunda = cliente_http.post("/v1/checkouts", json=cuerpo, headers=cabeceras())

    assert primera.status_code == 201
    assert primera.json()["reserva"]["estado"] == "ACTIVA"
    assert segunda.status_code == 409
    assert segunda.json()["codigo"] == "SIN_DISPONIBLE"
    assert segunda.json()["detalle"] == {"producto_id": str(producto_id)}


def test_un_cliente_con_checkout_activo_no_puede_abrir_otro():
    darkstore_id, producto_id = preparar_escenario(5)
    cliente = nuevo_cliente()
    lineas = [LineaSolicitud(producto_id=producto_id, cantidad=1)]
    with pool.connection() as conn:
        crear_checkout(conn, cliente, darkstore_id, lineas, uuid4())
    with pytest.raises(ErrorDeNegocio) as error:
        with pool.connection() as conn:
            crear_checkout(conn, cliente, darkstore_id, lineas, uuid4())
    assert error.value.codigo == "CHECKOUT_ACTIVO"
