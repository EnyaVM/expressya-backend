import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.db import pool
from app.errores import ErrorDeNegocio
from app.main import app
from app.modulos.reservas import (
    LineaSolicitud,
    crear_checkout,
    exigir_reserva_vigente,
    expirar_reserva,
    expirar_vencidas,
    liberar_reserva,
)
from escenarios import (
    crear_reserva_vencida,
    estados,
    movimientos_de,
    nuevo_cliente,
    preparar_escenario,
    reconstruir_saldos,
    saldos,
)

cliente_http = TestClient(app)


def reservar(cliente, darkstore_id, producto_id, cantidad=1):
    with pool.connection() as conn:
        respuesta = crear_checkout(
            conn, cliente, darkstore_id, [LineaSolicitud(producto_id=producto_id, cantidad=cantidad)], uuid4()
        )
    return respuesta["reserva"]["reserva_id"]


def liberar(cliente, reserva_id):
    with pool.connection() as conn:
        return liberar_reserva(conn, cliente, reserva_id, "CANCELACION_CLIENTE")


def expirar(reserva_id):
    with pool.connection() as conn:
        return expirar_reserva(conn, reserva_id)


def test_liberar_devuelve_las_unidades_y_deja_su_movimiento():
    darkstore_id, producto_id = preparar_escenario(5)
    cliente = nuevo_cliente()
    reserva_id = reservar(cliente, darkstore_id, producto_id, 2)
    assert saldos(darkstore_id, producto_id) == (5, 2, 3)

    respuesta = liberar(cliente, reserva_id)

    assert respuesta["estado"] == "LIBERADA"
    assert estados(reserva_id) == ("LIBERADA", "CANCELACION_CLIENTE", "SIN_COMPRA")
    assert saldos(darkstore_id, producto_id) == (5, 0, 5)
    assert movimientos_de(reserva_id) == [("RESERVA", "R", 2), ("LIBERACION", "R", -2)]


def test_liberar_dos_veces_da_estado_incompatible():
    darkstore_id, producto_id = preparar_escenario(3)
    cliente = nuevo_cliente()
    reserva_id = reservar(cliente, darkstore_id, producto_id)
    liberar(cliente, reserva_id)
    with pytest.raises(ErrorDeNegocio) as error:
        liberar(cliente, reserva_id)
    assert error.value.codigo == "ESTADO_INCOMPATIBLE"
    assert len(movimientos_de(reserva_id)) == 2


def test_no_se_puede_liberar_la_reserva_de_otro_cliente():
    darkstore_id, producto_id = preparar_escenario(3)
    reserva_id = reservar(nuevo_cliente(), darkstore_id, producto_id)
    with pytest.raises(ErrorDeNegocio) as error:
        liberar(nuevo_cliente(), reserva_id)
    assert error.value.codigo == "NO_ENCONTRADO"
    assert estados(reserva_id)[0] == "ACTIVA"


def test_la_unidad_liberada_la_puede_reservar_otro_cliente():
    darkstore_id, producto_id = preparar_escenario(1)
    primero, segundo = nuevo_cliente(), nuevo_cliente()
    reserva_id = reservar(primero, darkstore_id, producto_id)
    with pytest.raises(ErrorDeNegocio) as error:
        reservar(segundo, darkstore_id, producto_id)
    assert error.value.codigo == "SIN_DISPONIBLE"

    liberar(primero, reserva_id)

    reservar(segundo, darkstore_id, producto_id)
    assert saldos(darkstore_id, producto_id) == (1, 1, 0)


def test_una_reserva_vencida_expira_y_regresa_las_unidades():
    darkstore_id, producto_id = preparar_escenario(4)
    cliente = nuevo_cliente()
    _, reserva_id = crear_reserva_vencida(cliente, darkstore_id, producto_id, 3)
    assert saldos(darkstore_id, producto_id) == (4, 3, 1)

    assert expirar(reserva_id) is True

    assert estados(reserva_id) == ("EXPIRADA", "VENCIMIENTO", "SIN_COMPRA")
    assert saldos(darkstore_id, producto_id) == (4, 0, 4)
    assert movimientos_de(reserva_id) == [("RESERVA", "R", 3), ("EXPIRACION", "R", -3)]


def test_una_reserva_vigente_no_expira_antes_de_tiempo():
    darkstore_id, producto_id = preparar_escenario(2)
    reserva_id = reservar(nuevo_cliente(), darkstore_id, producto_id)
    assert expirar(reserva_id) is False
    assert estados(reserva_id)[0] == "ACTIVA"
    assert saldos(darkstore_id, producto_id) == (2, 1, 1)


def test_el_proceso_de_expiracion_cierra_solo_las_vencidas():
    darkstore_id, producto_id = preparar_escenario(10)
    _, vencida_1 = crear_reserva_vencida(nuevo_cliente(), darkstore_id, producto_id, 2)
    _, vencida_2 = crear_reserva_vencida(nuevo_cliente(), darkstore_id, producto_id, 3)
    vigente = reservar(nuevo_cliente(), darkstore_id, producto_id, 1)

    assert expirar_vencidas() >= 2

    assert estados(vencida_1)[0] == "EXPIRADA"
    assert estados(vencida_2)[0] == "EXPIRADA"
    assert estados(vigente)[0] == "ACTIVA"
    assert saldos(darkstore_id, producto_id) == (10, 1, 9)
    assert expirar_vencidas() == 0


def test_al_usar_una_reserva_se_comprueba_que_siga_vigente():
    darkstore_id, producto_id = preparar_escenario(5)
    cliente = nuevo_cliente()

    vigente = reservar(cliente, darkstore_id, producto_id)
    with pool.connection() as conn:
        exigir_reserva_vigente(conn, vigente)
    liberar(cliente, vigente)
    with pool.connection() as conn:
        with pytest.raises(ErrorDeNegocio) as liberada:
            exigir_reserva_vigente(conn, vigente)
    assert liberada.value.codigo == "ESTADO_INCOMPATIBLE"

    _, vencida = crear_reserva_vencida(nuevo_cliente(), darkstore_id, producto_id)
    with pool.connection() as conn:
        with pytest.raises(ErrorDeNegocio) as error:
            exigir_reserva_vigente(conn, vencida)
    assert error.value.codigo == "RESERVA_VENCIDA"


def test_un_cliente_con_reserva_vencida_puede_abrir_otro_checkout():
    darkstore_id, producto_id = preparar_escenario(5)
    cliente = nuevo_cliente()
    _, vieja = crear_reserva_vencida(cliente, darkstore_id, producto_id, 2)

    nueva = reservar(cliente, darkstore_id, producto_id, 1)

    assert estados(vieja)[0] == "EXPIRADA"
    assert estados(nueva)[0] == "ACTIVA"
    assert saldos(darkstore_id, producto_id) == (5, 1, 4)


def test_liberar_y_expirar_a_la_vez_cierra_la_reserva_una_sola_vez():
    darkstore_id, producto_id = preparar_escenario(3)
    cliente = nuevo_cliente()
    _, reserva_id = crear_reserva_vencida(cliente, darkstore_id, producto_id, 2)
    hilos = 8
    salida = threading.Barrier(hilos)

    def intentar(numero):
        salida.wait()
        try:
            if numero % 2 == 0:
                liberar(cliente, reserva_id)
                return "LIBERO"
            return "EXPIRO" if expirar(reserva_id) else "NADA"
        except ErrorDeNegocio as error:
            return error.codigo

    with ThreadPoolExecutor(max_workers=hilos) as ejecutor:
        resultados = list(ejecutor.map(intentar, range(hilos)))

    assert resultados.count("LIBERO") + resultados.count("EXPIRO") == 1
    assert estados(reserva_id)[0] in ("LIBERADA", "EXPIRADA")
    assert saldos(darkstore_id, producto_id) == (3, 0, 3)
    assert sorted(m[2] for m in movimientos_de(reserva_id)) == [-2, 2]


def test_el_saldo_se_reconstruye_desde_los_movimientos():
    darkstore_id, producto_id = preparar_escenario(6)
    a, b, c = nuevo_cliente(), nuevo_cliente(), nuevo_cliente()
    reserva_a = reservar(a, darkstore_id, producto_id, 2)
    reservar(b, darkstore_id, producto_id, 1)
    crear_reserva_vencida(c, darkstore_id, producto_id, 3)
    liberar(a, reserva_a)
    expirar_vencidas()

    e, r, d = saldos(darkstore_id, producto_id)
    e_movimientos, r_movimientos = reconstruir_saldos(darkstore_id, producto_id)

    assert (e, r) == (e_movimientos, r_movimientos) == (6, 1)
    assert d == 5


def test_post_cierre_responde_200_y_luego_409():
    darkstore_id, producto_id = preparar_escenario(2)
    cliente = nuevo_cliente()
    reserva_id = reservar(cliente, darkstore_id, producto_id)
    cabeceras = {"Clave-Operacion": str(uuid4()), "Cliente-Id": str(cliente)}
    cuerpo = {"motivo": "CANCELACION_CLIENTE"}

    primera = cliente_http.post(f"/v1/reservas/{reserva_id}/cierre", json=cuerpo, headers=cabeceras)
    segunda = cliente_http.post(f"/v1/reservas/{reserva_id}/cierre", json=cuerpo, headers=cabeceras)

    assert primera.status_code == 200
    assert primera.json()["estado"] == "LIBERADA"
    assert segunda.status_code == 409
    assert segunda.json()["codigo"] == "ESTADO_INCOMPATIBLE"