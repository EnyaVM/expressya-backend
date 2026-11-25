from uuid import uuid4

import pytest

from app.db import pool
from app.modulos import pasarela_simulada
from escenarios_c3 import lineas_de, pedido_pagado, tienda_con_productos
from escenarios_faltantes import (
    client,
    discrepancias,
    eventos_ev03,
    historial_cuadra,
    inventario,
    nuevo_usuario,
    reportar_faltante,
    reservar,
)


@pytest.fixture(autouse=True)
def restaurar_pasarela():
    pasarela_simulada.reiniciar_simulador()
    yield
    pasarela_simulada.reiniciar_simulador()


def separar(linea_id, usuario_id, cantidad=1):
    return client.post(
        f"/v1/lineas-pedido/{linea_id}/separacion",
        headers={"Clave-Operacion": str(uuid4()), "Usuario-Id": str(usuario_id)},
        json={"cantidad": cantidad},
    )


def test_picker_reporta_faltante_y_bloquea_solo_ese_producto():
    tienda, productos = tienda_con_productos(5, unidades=10)
    pedido_id, _, _ = pedido_pagado(tienda, [(p, 2) for p in productos])
    picker = nuevo_usuario(tienda, "PICKER")
    faltante = productos[2]

    respuesta = reportar_faltante(lineas_de(pedido_id)[faltante]["linea_id"], picker)

    assert respuesta.status_code == 201, respuesta.text
    cuerpo = respuesta.json()
    assert (cuerpo["estado"], cuerpo["origen"]) == ("EN_VALIDACION", "REPORTE_PICKER")
    assert inventario(tienda, faltante)["estado"] == "BLOQUEADO"
    assert all(inventario(tienda, p)["estado"] == "HABILITADO" for p in productos if p != faltante)
    lineas = lineas_de(pedido_id)
    assert lineas[faltante]["estado"] == "NO_SURTIBLE"
    assert all(lineas[p]["estado"] == "ASIGNADA" for p in productos if p != faltante)
    assert discrepancias(tienda, faltante) == [("FALTANTE", "REPORTE_PICKER", "EN_VALIDACION")]
    assert eventos_ev03(tienda, faltante) == [("EV-03", "M05")]
    assert historial_cuadra(tienda, faltante)


def test_at08_las_otras_lineas_se_siguen_separando_y_la_faltante_no():
    tienda, productos = tienda_con_productos(5, unidades=10)
    pedido_id, _, _ = pedido_pagado(tienda, [(p, 2) for p in productos])
    picker = nuevo_usuario(tienda, "PICKER")
    lineas = lineas_de(pedido_id)
    reportar_faltante(lineas[productos[2]]["linea_id"], picker)

    otras = [separar(lineas[p]["linea_id"], picker, 2) for p in productos if p != productos[2]]
    bloqueada = separar(lineas[productos[2]]["linea_id"], picker, 1)

    assert [r.status_code for r in otras] == [200, 200, 200, 200]
    assert bloqueada.status_code == 409
    assert {l["estado"] for p, l in lineas_de(pedido_id).items() if p != productos[2]} == {"SEPARADA"}


def test_el_faltante_marca_las_lineas_de_otros_pedidos_y_retira_reservas_activas():
    tienda, (producto, otro) = tienda_con_productos(2, unidades=10)
    pedido_a, _, _ = pedido_pagado(tienda, [(producto, 2)])
    pedido_b, _, _ = pedido_pagado(tienda, [(producto, 1)])
    reserva = reservar_varios(tienda, [(producto, 1), (otro, 1)])
    picker = nuevo_usuario(tienda, "PICKER")

    reportar_faltante(lineas_de(pedido_a)[producto]["linea_id"], picker)

    assert lineas_de(pedido_b)[producto]["estado"] == "NO_SURTIBLE"
    assert inventario(tienda, producto)["r"] == 0
    assert inventario(tienda, otro)["r"] == 1
    with pool.connection() as conn:
        estado, total = conn.execute(
            "SELECT r.estado, p.total_vigente FROM reserva r JOIN pedido p USING (pedido_id) WHERE r.reserva_id = %s",
            (reserva["reserva"]["reserva_id"],),
        ).fetchone()
    assert (estado, str(total)) == ("ACTIVA", "100.00")
    with pool.connection() as conn:
        retiro = conn.execute(
            "SELECT ms.saldo, ms.cantidad FROM movimiento_inventario m JOIN movimiento_saldo ms USING (movimiento_id) "
            "WHERE m.tipo = 'RETIRO_RESERVA' AND m.referencia_reserva_id = %s",
            (reserva["reserva"]["reserva_id"],),
        ).fetchall()
    assert retiro == [("R", -1)]


def reservar_varios(tienda, lineas):
    from escenarios import nuevo_cliente

    respuesta = client.post(
        "/v1/checkouts",
        headers={"Clave-Operacion": str(uuid4()), "Cliente-Id": str(nuevo_cliente())},
        json={"darkstore_id": str(tienda), "lineas": [{"producto_id": str(p), "cantidad": c} for p, c in lineas]},
    )
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()


def test_repetir_el_reporte_con_la_misma_clave_no_abre_otra_discrepancia():
    tienda, (producto,) = tienda_con_productos(1, unidades=10)
    pedido_id, _, _ = pedido_pagado(tienda, [(producto, 2)])
    picker = nuevo_usuario(tienda, "PICKER")
    linea = lineas_de(pedido_id)[producto]["linea_id"]
    clave = uuid4()

    primera = reportar_faltante(linea, picker, clave)
    segunda = reportar_faltante(linea, picker, clave)

    assert segunda.headers.get("Resultado-Previo") == "true"
    assert segunda.json() == primera.json()
    assert len(discrepancias(tienda, producto)) == 1


def test_personal_de_tienda_puede_reportar_y_responsable_no():
    tienda, (producto,) = tienda_con_productos(1, unidades=10)
    pedido_id, _, _ = pedido_pagado(tienda, [(producto, 2)])
    linea = lineas_de(pedido_id)[producto]["linea_id"]

    rechazo = reportar_faltante(linea, nuevo_usuario(tienda, "RESPONSABLE"))
    aceptado = reportar_faltante(linea, nuevo_usuario(tienda, "PERSONAL_TIENDA"))

    assert rechazo.status_code == 403
    assert aceptado.status_code == 201


def test_linea_ya_separada_no_admite_reporte():
    tienda, (producto,) = tienda_con_productos(1, unidades=10)
    pedido_id, _, _ = pedido_pagado(tienda, [(producto, 1)])
    picker = nuevo_usuario(tienda, "PICKER")
    linea = lineas_de(pedido_id)[producto]["linea_id"]
    assert separar(linea, picker, 1).status_code == 200

    respuesta = reportar_faltante(linea, picker)

    assert respuesta.status_code == 409
    assert respuesta.json()["codigo"] == "ESTADO_INCOMPATIBLE"
    assert discrepancias(tienda, producto) == []


def test_linea_inexistente_responde_404_y_sin_evidencia_400():
    tienda, (producto,) = tienda_con_productos(1, unidades=10)
    pedido_id, _, _ = pedido_pagado(tienda, [(producto, 1)])
    picker = nuevo_usuario(tienda, "PICKER")

    assert reportar_faltante(uuid4(), picker).status_code == 404
    sin_evidencia = reportar_faltante(lineas_de(pedido_id)[producto]["linea_id"], picker, evidencia="")
    assert sin_evidencia.status_code == 400
    assert inventario(tienda, producto)["estado"] == "HABILITADO"


def test_reserva_que_solo_tenia_el_producto_faltante_queda_liberada():
    tienda, (producto,) = tienda_con_productos(1, unidades=10)
    pedido_id, _, _ = pedido_pagado(tienda, [(producto, 2)])
    reserva = reservar(tienda, producto, 3)
    picker = nuevo_usuario(tienda, "PICKER")

    reportar_faltante(lineas_de(pedido_id)[producto]["linea_id"], picker)

    with pool.connection() as conn:
        estado, motivo, pedido = conn.execute(
            "SELECT r.estado, r.motivo_cierre, p.estado FROM reserva r JOIN pedido p USING (pedido_id) WHERE r.reserva_id = %s",
            (reserva["reserva"]["reserva_id"],),
        ).fetchone()
        avisos = conn.execute(
            "SELECT tipo FROM aviso WHERE origen_id = %s", (reserva["reserva"]["reserva_id"],)
        ).fetchall()
    assert (estado, motivo, pedido) == ("LIBERADA", "FALTANTE", "SIN_COMPRA")
    assert avisos == [("PRODUCTO_RETIRADO",)]
