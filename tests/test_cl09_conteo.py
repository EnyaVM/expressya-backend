from uuid import uuid4

import pytest

from app.db import pool
from app.modulos import pasarela_simulada
from escenarios_c3 import lineas_de, pedido_pagado, tienda_con_productos
from escenarios_faltantes import (
    contar,
    discrepancias,
    eventos_ev03,
    historial_cuadra,
    inventario,
    nuevo_usuario,
    reservar,
)


@pytest.fixture(autouse=True)
def restaurar_pasarela():
    pasarela_simulada.reiniciar_simulador()
    yield
    pasarela_simulada.reiniciar_simulador()


def test_conteo_igual_a_existencias_solo_queda_registrado():
    tienda, (producto,) = tienda_con_productos(1, unidades=10)
    picker = nuevo_usuario(tienda, "PICKER")

    respuesta = contar(tienda, producto, picker, 10)

    assert respuesta.status_code == 201, respuesta.text
    cuerpo = respuesta.json()
    assert (cuerpo["cantidad_contada"], cuerpo["e_al_corte"], cuerpo["discrepancia_id"]) == (10, 10, None)
    assert inventario(tienda, producto) == {"e": 10, "n": 0, "r": 0, "c": 0, "d": 10, "estado": "HABILITADO"}
    assert discrepancias(tienda, producto) == []


def test_at07_diez_registradas_ocho_contadas_deja_dos_en_revision():
    tienda, (producto,) = tienda_con_productos(1, unidades=10)
    reservar(tienda, producto, 3)
    picker = nuevo_usuario(tienda, "PICKER")

    respuesta = contar(tienda, producto, picker, 8)

    assert respuesta.status_code == 201, respuesta.text
    assert respuesta.json()["discrepancia_id"] is not None
    assert inventario(tienda, producto) == {"e": 8, "n": 2, "r": 3, "c": 0, "d": 5, "estado": "HABILITADO"}
    assert discrepancias(tienda, producto) == [("DIFERENCIA_SIN_FALTANTE", "CONTEO", "EN_VALIDACION")]
    assert eventos_ev03(tienda, producto) == [("EV-03", "M01")]
    assert historial_cuadra(tienda, producto)


def test_conteo_menor_que_lo_comprometido_bloquea_y_retira_reservas():
    tienda, (producto,) = tienda_con_productos(1, unidades=10)
    pedido_id, _, _ = pedido_pagado(tienda, [(producto, 7)])
    reserva = reservar(tienda, producto, 2)
    picker = nuevo_usuario(tienda, "PICKER")

    respuesta = contar(tienda, producto, picker, 5)

    assert respuesta.status_code == 201, respuesta.text
    assert inventario(tienda, producto) == {"e": 5, "n": 5, "r": 0, "c": 7, "d": -2, "estado": "BLOQUEADO"}
    assert discrepancias(tienda, producto) == [("FALTANTE", "CONTEO", "EN_VALIDACION")]
    assert lineas_de(pedido_id)[producto]["estado"] == "NO_SURTIBLE"
    with pool.connection() as conn:
        estado_reserva, estado_pedido = conn.execute(
            "SELECT r.estado, p.estado FROM reserva r JOIN pedido p USING (pedido_id) WHERE r.reserva_id = %s",
            (reserva["reserva"]["reserva_id"],),
        ).fetchone()
    assert (estado_reserva, estado_pedido) == ("LIBERADA", "SIN_COMPRA")
    assert historial_cuadra(tienda, producto)


def test_sobrante_abre_discrepancia_sin_mover_existencias():
    tienda, (producto,) = tienda_con_productos(1, unidades=10)
    picker = nuevo_usuario(tienda, "PICKER")

    respuesta = contar(tienda, producto, picker, 12)

    assert respuesta.status_code == 201
    assert inventario(tienda, producto) == {"e": 10, "n": 0, "r": 0, "c": 0, "d": 10, "estado": "HABILITADO"}
    assert discrepancias(tienda, producto) == [("DIFERENCIA_SIN_FALTANTE", "CONTEO", "EN_VALIDACION")]


def test_repetir_el_mismo_conteo_no_duplica_la_discrepancia():
    tienda, (producto,) = tienda_con_productos(1, unidades=10)
    picker = nuevo_usuario(tienda, "PICKER")
    clave = uuid4()

    primera = contar(tienda, producto, picker, 8, clave)
    segunda = contar(tienda, producto, picker, 8, clave)

    assert segunda.status_code == 201
    assert segunda.headers.get("Resultado-Previo") == "true"
    assert segunda.json() == primera.json()
    assert len(discrepancias(tienda, producto)) == 1
    assert inventario(tienda, producto)["e"] == 8


@pytest.mark.parametrize("roles", [(), ("PERSONAL_TIENDA",), ("RESPONSABLE",)])
def test_sin_rol_de_conteo_en_la_tienda_responde_403(roles):
    tienda, (producto,) = tienda_con_productos(1, unidades=10)
    usuario = nuevo_usuario(tienda, *roles)

    respuesta = contar(tienda, producto, usuario, 8)

    assert respuesta.status_code == 403
    assert respuesta.json()["codigo"] == "SIN_PERMISO"
    assert inventario(tienda, producto)["e"] == 10


def test_supervisor_tambien_puede_contar():
    tienda, (producto,) = tienda_con_productos(1, unidades=10)
    supervisor = nuevo_usuario(tienda, "SUPERVISOR")

    assert contar(tienda, producto, supervisor, 10).status_code == 201


def test_cantidad_negativa_responde_400():
    tienda, (producto,) = tienda_con_productos(1, unidades=10)
    picker = nuevo_usuario(tienda, "PICKER")

    respuesta = contar(tienda, producto, picker, -1)

    assert respuesta.status_code == 400
    assert respuesta.json()["codigo"] == "SOLICITUD_INVALIDA"


def test_producto_inexistente_en_la_tienda_responde_404():
    tienda, _ = tienda_con_productos(1, unidades=10)
    picker = nuevo_usuario(tienda, "PICKER")

    respuesta = contar(tienda, uuid4(), picker, 3)

    assert respuesta.status_code == 404


def test_contar_exactamente_lo_comprometido_es_diferencia_y_no_faltante():
    tienda, (producto,) = tienda_con_productos(1, unidades=10)
    pedido_pagado(tienda, [(producto, 8)])
    picker = nuevo_usuario(tienda, "PICKER")

    respuesta = contar(tienda, producto, picker, 8)

    assert respuesta.json()["tipo_discrepancia"] == "DIFERENCIA_SIN_FALTANTE"
    assert inventario(tienda, producto) == {"e": 8, "n": 2, "r": 0, "c": 8, "d": 0, "estado": "HABILITADO"}


def test_una_sola_unidad_de_diferencia_tambien_pasa_a_revision():
    tienda, (producto,) = tienda_con_productos(1, unidades=10)
    picker = nuevo_usuario(tienda, "PICKER")

    contar(tienda, producto, picker, 9)

    assert (inventario(tienda, producto)["e"], inventario(tienda, producto)["n"]) == (9, 1)
    assert historial_cuadra(tienda, producto)
