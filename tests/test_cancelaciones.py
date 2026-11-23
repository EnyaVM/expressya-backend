from decimal import Decimal
from uuid import uuid4

import pytest

from app.db import pool
from app.modulos import pasarela_simulada
from escenarios import nuevo_cliente
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


def test_cliente_cancela_pedido_confirmado_y_recibe_reembolso_total():
    darkstore_id, (producto_id,) = tienda_con_productos(1, unidades=10)
    pedido_id, cliente_id, intento_id = pedido_pagado(darkstore_id, [(producto_id, 3)])
    assert saldos_de(darkstore_id, producto_id) == (10, 0, 3, 7)

    respuesta = cancelar(pedido_id, cliente_id)

    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["estado"] == "CANCELADO"
    assert cuerpo["monto_reembolso"] == "300.00"
    assert estado_pedido(pedido_id) == "CANCELADO"
    linea = lineas_de(pedido_id)[producto_id]
    assert (linea["k"], linea["estado"]) == (3, "CANCELADA")
    assert saldos_de(darkstore_id, producto_id) == (10, 0, 0, 10)
    reembolsos = reembolsos_de(intento_id)
    assert len(reembolsos) == 1
    assert reembolsos[0][1] == Decimal("300.00")
    assert reembolsos[0][4] is not None
    assert estado_intento(intento_id) == "REEMBOLSADO_TOTAL"


def test_repetir_la_misma_cancelacion_no_duplica_el_reembolso():
    darkstore_id, (producto_id,) = tienda_con_productos(1)
    pedido_id, cliente_id, intento_id = pedido_pagado(darkstore_id, [(producto_id, 2)])
    clave = uuid4()

    primera = cancelar(pedido_id, cliente_id, clave)
    segunda = cancelar(pedido_id, cliente_id, clave)

    assert primera.status_code == segunda.status_code == 200
    assert segunda.headers.get("Resultado-Previo") == "true"
    assert segunda.json() == primera.json()
    assert len(reembolsos_de(intento_id)) == 1


def test_misma_clave_con_otro_motivo_es_conflicto():
    darkstore_id, (producto_id,) = tienda_con_productos(1)
    pedido_id, cliente_id, _ = pedido_pagado(darkstore_id, [(producto_id, 1)])
    clave = uuid4()

    assert cancelar(pedido_id, cliente_id, clave, "motivo A").status_code == 200
    conflicto = cancelar(pedido_id, cliente_id, clave, "motivo B")

    assert conflicto.status_code == 409
    assert conflicto.json()["codigo"] == "CONFLICTO_DE_CLAVE"


def test_cancelar_de_nuevo_con_otra_clave_no_genera_otro_reembolso():
    darkstore_id, (producto_id,) = tienda_con_productos(1)
    pedido_id, cliente_id, intento_id = pedido_pagado(darkstore_id, [(producto_id, 1)])

    assert cancelar(pedido_id, cliente_id).status_code == 200
    otra = cancelar(pedido_id, cliente_id)

    assert otra.status_code == 409
    assert otra.json()["codigo"] == "ESTADO_INCOMPATIBLE"
    assert len(reembolsos_de(intento_id)) == 1


def test_preparacion_iniciada_impide_la_cancelacion_del_cliente():
    darkstore_id, (producto_id,) = tienda_con_productos(1)
    pedido_id, cliente_id, intento_id = pedido_pagado(darkstore_id, [(producto_id, 2)])
    with pool.connection() as conn:
        conn.execute("UPDATE linea_pedido SET s = 1 WHERE pedido_id = %s", (pedido_id,))

    respuesta = cancelar(pedido_id, cliente_id)

    assert respuesta.status_code == 409
    assert respuesta.json()["codigo"] == "PREPARACION_INICIADA"
    assert estado_pedido(pedido_id) == "CONFIRMADO"
    assert lineas_de(pedido_id)[producto_id]["k"] == 0
    assert reembolsos_de(intento_id) == []


def test_otro_cliente_no_puede_cancelar_el_pedido():
    darkstore_id, (producto_id,) = tienda_con_productos(1)
    pedido_id, _, _ = pedido_pagado(darkstore_id, [(producto_id, 1)])

    respuesta = cancelar(pedido_id, nuevo_cliente())

    assert respuesta.status_code == 404
    assert estado_pedido(pedido_id) == "CONFIRMADO"


def test_sin_cliente_id_responde_401():
    from escenarios_c3 import client

    respuesta = client.post(
        f"/v1/pedidos/{uuid4()}/cancelacion",
        headers={"Clave-Operacion": str(uuid4())},
        json={"motivo": "x"},
    )
    assert respuesta.status_code == 401


def test_faltante_cancela_una_linea_de_cinco_con_reembolso_parcial():
    darkstore_id, productos = tienda_con_productos(5, unidades=10, precio="100.00")
    pedido_id, _, intento_id = pedido_pagado(darkstore_id, [(p, 2) for p in productos])
    faltante = productos[2]
    linea_id = lineas_de(pedido_id)[faltante]["linea_id"]
    resolucion_id = crear_resolucion_faltante(darkstore_id, faltante, linea_id, canceladas=1)

    aplicar_faltante(resolucion_id, linea_id)

    lineas = lineas_de(pedido_id)
    assert (lineas[faltante]["k"], lineas[faltante]["estado"]) == (1, "ASIGNADA")
    assert all(lineas[p]["k"] == 0 for p in productos if p != faltante)
    assert estado_pedido(pedido_id) == "CONFIRMADO"
    with pool.connection() as conn:
        total = conn.execute("SELECT total_vigente FROM pedido WHERE pedido_id = %s", (pedido_id,)).fetchone()[0]
        cancelacion = conn.execute(
            "SELECT cantidad, causa, usuario_id, reembolso_id FROM cancelacion_linea WHERE linea_id = %s",
            (linea_id,),
        ).fetchone()
    assert total == Decimal("900.00")
    assert cancelacion[0] == 1 and cancelacion[1] == "FALTANTE_VALIDADO" and cancelacion[2] is not None
    reembolsos = reembolsos_de(intento_id)
    assert len(reembolsos) == 1
    assert reembolsos[0][1] == Decimal("100.00")
    assert reembolsos[0][0] == cancelacion[3]
    assert estado_intento(intento_id) == "REEMBOLSADO_PARCIAL"
    assert saldos_de(darkstore_id, faltante)[2] == 1


def test_aplicar_dos_veces_el_mismo_faltante_no_duplica_el_reembolso():
    darkstore_id, (producto_id,) = tienda_con_productos(1)
    pedido_id, _, intento_id = pedido_pagado(darkstore_id, [(producto_id, 3)])
    linea_id = lineas_de(pedido_id)[producto_id]["linea_id"]
    resolucion_id = crear_resolucion_faltante(darkstore_id, producto_id, linea_id, canceladas=2)

    aplicar_faltante(resolucion_id, linea_id)
    segunda = aplicar_faltante(resolucion_id, linea_id)

    assert segunda is None
    assert lineas_de(pedido_id)[producto_id]["k"] == 2
    assert len(reembolsos_de(intento_id)) == 1


def test_faltante_de_todas_las_unidades_cancela_el_pedido():
    darkstore_id, (producto_id,) = tienda_con_productos(1)
    pedido_id, _, intento_id = pedido_pagado(darkstore_id, [(producto_id, 2)])
    linea_id = lineas_de(pedido_id)[producto_id]["linea_id"]
    resolucion_id = crear_resolucion_faltante(darkstore_id, producto_id, linea_id, canceladas=2)

    aplicar_faltante(resolucion_id, linea_id)

    assert lineas_de(pedido_id)[producto_id]["estado"] == "CANCELADA"
    assert estado_pedido(pedido_id) == "CANCELADO"
    assert estado_intento(intento_id) == "REEMBOLSADO_TOTAL"


def test_lo_reembolsado_nunca_supera_lo_cobrado():
    darkstore_id, productos = tienda_con_productos(2)
    pedido_id, cliente_id, intento_id = pedido_pagado(darkstore_id, [(p, 2) for p in productos])
    linea_id = lineas_de(pedido_id)[productos[0]]["linea_id"]
    aplicar_faltante(crear_resolucion_faltante(darkstore_id, productos[0], linea_id, canceladas=1), linea_id)

    assert cancelar(pedido_id, cliente_id).status_code == 200

    reembolsos = reembolsos_de(intento_id)
    assert sum(r[1] for r in reembolsos) == Decimal("400.00")
    assert all(r[4] is not None for r in reembolsos)
    assert estado_intento(intento_id) == "REEMBOLSADO_TOTAL"
