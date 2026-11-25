from decimal import Decimal
from uuid import uuid4

import pytest

from app.db import pool
from app.modulos import pasarela_simulada
from escenarios_c3 import estado_intento, estado_pedido, lineas_de, pedido_pagado, reembolsos_de, tienda_con_productos
from escenarios_faltantes import (
    client,
    contar,
    discrepancias,
    historial_cuadra,
    inventario,
    nuevo_usuario,
    reportar_faltante,
    reservar,
    resolver,
)


@pytest.fixture(autouse=True)
def restaurar_pasarela():
    pasarela_simulada.reiniciar_simulador()
    yield
    pasarela_simulada.reiniciar_simulador()


def separar(linea_id, usuario_id, cantidad):
    return client.post(
        f"/v1/lineas-pedido/{linea_id}/separacion",
        headers={"Clave-Operacion": str(uuid4()), "Usuario-Id": str(usuario_id)},
        json={"cantidad": cantidad},
    )


def diez_contra_ocho():
    tienda, (producto,) = tienda_con_productos(1, unidades=10)
    reservar(tienda, producto, 3)
    picker = nuevo_usuario(tienda, "PICKER")
    supervisor = nuevo_usuario(tienda, "SUPERVISOR")
    conteo = contar(tienda, producto, picker, 8).json()
    return tienda, producto, supervisor, conteo


@pytest.mark.parametrize("validadas, esperado", [(9, (9, 0, 6)), (10, (10, 0, 7)), (8, (8, 0, 5))])
def test_at07_resolver_la_diferencia_ajusta_existencias_y_libera_lo_que_estaba_en_revision(validadas, esperado):
    tienda, producto, supervisor, conteo = diez_contra_ocho()

    respuesta = resolver(conteo["discrepancia_id"], supervisor, conteo["conteo_id"], validadas)

    assert respuesta.status_code == 200, respuesta.text
    saldos = inventario(tienda, producto)
    assert (saldos["e"], saldos["n"], saldos["d"]) == esperado
    assert saldos["estado"] == "HABILITADO"
    assert discrepancias(tienda, producto) == [("DIFERENCIA_SIN_FALTANTE", "CONTEO", "RESUELTA")]
    assert historial_cuadra(tienda, producto)


def test_la_resolucion_queda_en_el_historial_con_quien_la_autorizo():
    tienda, producto, supervisor, conteo = diez_contra_ocho()

    resolver(conteo["discrepancia_id"], supervisor, conteo["conteo_id"], 9)

    with pool.connection() as conn:
        movimiento = conn.execute(
            "SELECT motivo, usuario_id, autorizado_por FROM movimiento_inventario "
            "WHERE darkstore_id = %s AND producto_id = %s AND tipo = 'RESOLUCION'",
            (tienda, producto),
        ).fetchall()
        resolucion = conn.execute(
            "SELECT supervisor_id, unidades_validadas, evidencia, regla_aplicada FROM resolucion_faltante WHERE discrepancia_id = %s",
            (conteo["discrepancia_id"],),
        ).fetchone()
    assert movimiento == [("ajuste_conteo", supervisor, supervisor)]
    assert resolucion == (supervisor, 9, "Recuento con foto del anaquel", "Ajuste de conteo validado (P4, P8)")


def test_sobrante_validado_sube_existencias():
    tienda, (producto,) = tienda_con_productos(1, unidades=10)
    picker, supervisor = nuevo_usuario(tienda, "PICKER"), nuevo_usuario(tienda, "SUPERVISOR")
    conteo = contar(tienda, producto, picker, 12).json()

    assert resolver(conteo["discrepancia_id"], supervisor, conteo["conteo_id"], 12).status_code == 200

    assert inventario(tienda, producto)["e"] == 12
    assert historial_cuadra(tienda, producto)


def test_at08_cinco_lineas_una_faltante_cancela_solo_lo_que_falta_y_las_otras_continuan():
    tienda, productos = tienda_con_productos(5, unidades=10)
    pedido_id, _, intento_id = pedido_pagado(tienda, [(p, 2) for p in productos])
    picker, supervisor = nuevo_usuario(tienda, "PICKER"), nuevo_usuario(tienda, "SUPERVISOR")
    faltante = productos[2]
    lineas = lineas_de(pedido_id)
    reporte = reportar_faltante(lineas[faltante]["linea_id"], picker).json()
    conteo = contar(tienda, faltante, supervisor, 1).json()
    assert conteo["discrepancia_id"] == reporte["discrepancia_id"]

    respuesta = resolver(reporte["discrepancia_id"], supervisor, conteo["conteo_id"], 1)

    assert respuesta.status_code == 200, respuesta.text
    asignacion = respuesta.json()["asignaciones"]
    assert [(a["asignadas"], a["canceladas"]) for a in asignacion] == [(1, 1)]
    linea = lineas_de(pedido_id)[faltante]
    assert (linea["k"], linea["estado"]) == (1, "ASIGNADA")
    assert inventario(tienda, faltante) == {"e": 1, "n": 0, "r": 0, "c": 1, "d": 0, "estado": "HABILITADO"}
    assert [r[1] for r in reembolsos_de(intento_id)] == [Decimal("100.00")]
    assert estado_intento(intento_id) == "REEMBOLSADO_PARCIAL"
    for p in productos:
        restante = lineas_de(pedido_id)[p]
        assert separar(restante["linea_id"], picker, restante["q"] - restante["s"] - restante["k"]).status_code == 200
    estados = {p: l["estado"] for p, l in lineas_de(pedido_id).items()}
    assert estados[faltante] == "PARCIAL"
    assert {estados[p] for p in productos if p != faltante} == {"SEPARADA"}
    assert estado_pedido(pedido_id) == "CONFIRMADO"
    assert historial_cuadra(tienda, faltante)


def test_d05_reparte_por_orden_de_compromiso_y_cancela_al_ultimo():
    tienda, (producto,) = tienda_con_productos(1, unidades=10)
    pedido_a, _, intento_a = pedido_pagado(tienda, [(producto, 5)])
    pedido_b, _, intento_b = pedido_pagado(tienda, [(producto, 4)])
    reserva_c = reservar(tienda, producto, 1)
    picker, supervisor = nuevo_usuario(tienda, "PICKER"), nuevo_usuario(tienda, "SUPERVISOR")
    conteo = contar(tienda, producto, picker, 8).json()
    assert conteo["tipo_discrepancia"] == "FALTANTE"

    respuesta = resolver(conteo["discrepancia_id"], supervisor, conteo["conteo_id"], 8)

    assert respuesta.status_code == 200, respuesta.text
    assert (lineas_de(pedido_a)[producto]["k"], lineas_de(pedido_b)[producto]["k"]) == (0, 1)
    assert reembolsos_de(intento_a) == []
    with pool.connection() as conn:
        regla = conn.execute(
            "SELECT regla_aplicada FROM resolucion_faltante WHERE discrepancia_id = %s", (conteo["discrepancia_id"],)
        ).fetchone()[0]
    assert regla.startswith("D-05")
    assert [r[1] for r in reembolsos_de(intento_b)] == [Decimal("100.00")]
    assert inventario(tienda, producto) == {"e": 8, "n": 0, "r": 0, "c": 8, "d": 0, "estado": "HABILITADO"}
    with pool.connection() as conn:
        assert conn.execute("SELECT estado FROM reserva WHERE reserva_id = %s", (reserva_c["reserva"]["reserva_id"],)).fetchone()[0] == "LIBERADA"
    assert historial_cuadra(tienda, producto)


def test_las_unidades_ya_separadas_se_quedan_con_su_pedido():
    tienda, (producto,) = tienda_con_productos(1, unidades=10)
    pedido_a, _, _ = pedido_pagado(tienda, [(producto, 3)])
    pedido_b, _, intento_b = pedido_pagado(tienda, [(producto, 3)])
    picker, supervisor = nuevo_usuario(tienda, "PICKER"), nuevo_usuario(tienda, "SUPERVISOR")
    assert separar(lineas_de(pedido_b)[producto]["linea_id"], picker, 2).status_code == 200
    reporte = reportar_faltante(lineas_de(pedido_a)[producto]["linea_id"], picker).json()
    conteo = contar(tienda, producto, supervisor, 3).json()

    respuesta = resolver(reporte["discrepancia_id"], supervisor, conteo["conteo_id"], 3)

    asignaciones = {a["linea_id"]: a for a in respuesta.json()["asignaciones"]}
    b = asignaciones[str(lineas_de(pedido_b)[producto]["linea_id"])]
    assert (b["separadas_antes"], b["asignadas"], b["canceladas"]) == (2, 0, 1)
    assert lineas_de(pedido_a)[producto]["k"] == 0
    assert lineas_de(pedido_b)[producto]["estado"] == "PARCIAL"
    assert [r[1] for r in reembolsos_de(intento_b)] == [Decimal("100.00")]


def test_faltante_total_cancela_la_linea_y_el_pedido_de_un_solo_producto():
    tienda, (producto,) = tienda_con_productos(1, unidades=10)
    pedido_id, _, intento_id = pedido_pagado(tienda, [(producto, 2)])
    picker, supervisor = nuevo_usuario(tienda, "PICKER"), nuevo_usuario(tienda, "SUPERVISOR")
    reporte = reportar_faltante(lineas_de(pedido_id)[producto]["linea_id"], picker).json()
    conteo = contar(tienda, producto, supervisor, 0).json()

    assert resolver(reporte["discrepancia_id"], supervisor, conteo["conteo_id"], 0).status_code == 200

    assert lineas_de(pedido_id)[producto]["estado"] == "CANCELADA"
    assert estado_pedido(pedido_id) == "CANCELADO"
    assert estado_intento(intento_id) == "REEMBOLSADO_TOTAL"


def test_conteo_con_discrepancia_abierta_solo_se_agrega_como_evidencia():
    tienda, producto, supervisor, conteo = diez_contra_ocho()

    segundo = contar(tienda, producto, supervisor, 7).json()

    assert segundo["discrepancia_id"] == conteo["discrepancia_id"]
    assert len(discrepancias(tienda, producto)) == 1
    assert inventario(tienda, producto)["e"] == 8


def test_reporte_de_faltante_sobre_una_diferencia_abierta_la_convierte_en_faltante():
    tienda, (producto,) = tienda_con_productos(1, unidades=10)
    pedido_id, _, _ = pedido_pagado(tienda, [(producto, 2)])
    picker = nuevo_usuario(tienda, "PICKER")
    conteo = contar(tienda, producto, picker, 9).json()

    reporte = reportar_faltante(lineas_de(pedido_id)[producto]["linea_id"], picker)

    assert reporte.json()["discrepancia_id"] == conteo["discrepancia_id"]
    assert discrepancias(tienda, producto) == [("FALTANTE", "CONTEO", "EN_VALIDACION")]
    assert inventario(tienda, producto)["estado"] == "BLOQUEADO"


def test_resolver_dos_veces_da_409_y_la_misma_clave_devuelve_la_respuesta_guardada():
    tienda, producto, supervisor, conteo = diez_contra_ocho()
    clave = uuid4()

    primera = resolver(conteo["discrepancia_id"], supervisor, conteo["conteo_id"], 9, clave)
    repetida = resolver(conteo["discrepancia_id"], supervisor, conteo["conteo_id"], 9, clave)
    otra = resolver(conteo["discrepancia_id"], supervisor, conteo["conteo_id"], 9)

    assert repetida.headers.get("Resultado-Previo") == "true"
    assert repetida.json() == primera.json()
    assert otra.status_code == 409
    assert otra.json()["codigo"] == "ESTADO_INCOMPATIBLE"
    assert inventario(tienda, producto)["e"] == 9


@pytest.mark.parametrize("roles", [("PICKER",), ("RESPONSABLE",), ()])
def test_solo_el_supervisor_de_la_tienda_resuelve(roles):
    tienda, producto, _, conteo = diez_contra_ocho()

    respuesta = resolver(conteo["discrepancia_id"], nuevo_usuario(tienda, *roles), conteo["conteo_id"], 9)

    assert respuesta.status_code == 403
    assert discrepancias(tienda, producto)[0][2] == "EN_VALIDACION"


def test_unidades_fuera_de_rango_y_sin_evidencia_se_rechazan():
    tienda, producto, supervisor, conteo = diez_contra_ocho()

    fuera = resolver(conteo["discrepancia_id"], supervisor, conteo["conteo_id"], 11)
    sin_evidencia = resolver(conteo["discrepancia_id"], supervisor, conteo["conteo_id"], 9, evidencia="")

    assert (fuera.status_code, fuera.json()["codigo"]) == (400, "SOLICITUD_INVALIDA")
    assert sin_evidencia.status_code == 400
    assert inventario(tienda, producto)["e"] == 8


def test_conteo_de_otro_producto_o_anterior_a_la_discrepancia_no_sirve():
    tienda, (producto, otro) = tienda_con_productos(2, unidades=10)
    picker, supervisor = nuevo_usuario(tienda, "PICKER"), nuevo_usuario(tienda, "SUPERVISOR")
    viejo = contar(tienda, producto, picker, 10).json()
    ajeno = contar(tienda, otro, picker, 10).json()
    conteo = contar(tienda, producto, picker, 8).json()

    for conteo_id in (viejo["conteo_id"], ajeno["conteo_id"]):
        respuesta = resolver(conteo["discrepancia_id"], supervisor, conteo_id, 9)
        assert (respuesta.status_code, respuesta.json()["codigo"]) == (409, "ESTADO_INCOMPATIBLE")


def test_validar_menos_que_las_reservas_activas_las_retira():
    tienda, producto, supervisor, conteo = diez_contra_ocho()

    assert resolver(conteo["discrepancia_id"], supervisor, conteo["conteo_id"], 2).status_code == 200

    assert inventario(tienda, producto) == {"e": 2, "n": 0, "r": 0, "c": 0, "d": 2, "estado": "HABILITADO"}
    assert historial_cuadra(tienda, producto)


def test_validar_justo_lo_reservado_conserva_las_reservas():
    tienda, producto, supervisor, conteo = diez_contra_ocho()

    assert resolver(conteo["discrepancia_id"], supervisor, conteo["conteo_id"], 3).status_code == 200

    assert inventario(tienda, producto) == {"e": 3, "n": 0, "r": 3, "c": 0, "d": 0, "estado": "HABILITADO"}


def test_si_lo_validado_no_cubre_reservas_y_compromisos_primero_se_retiran_las_reservas():
    tienda, (producto,) = tienda_con_productos(1, unidades=10)
    pedido_id, _, intento_id = pedido_pagado(tienda, [(producto, 4)])
    reservar(tienda, producto, 3)
    picker, supervisor = nuevo_usuario(tienda, "PICKER"), nuevo_usuario(tienda, "SUPERVISOR")
    conteo = contar(tienda, producto, picker, 8).json()
    assert conteo["tipo_discrepancia"] == "DIFERENCIA_SIN_FALTANTE"

    respuesta = resolver(conteo["discrepancia_id"], supervisor, conteo["conteo_id"], 6)

    assert [(a["asignadas"], a["canceladas"]) for a in respuesta.json()["asignaciones"]] == [(4, 0)]
    assert inventario(tienda, producto) == {"e": 6, "n": 0, "r": 0, "c": 4, "d": 2, "estado": "HABILITADO"}
    assert reembolsos_de(intento_id) == []
