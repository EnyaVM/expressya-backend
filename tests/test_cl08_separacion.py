
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.db import pool
from escenarios import PICKER
from escenarios_c3 import (
    lineas_de,
    pedido_pagado,
    saldos_de,
    tienda_con_productos,
)

client = TestClient(app)


def test_cl08_separar_productos_correctamente():
    # Crear una tienda con 10 unidades.
    tienda_id, productos = tienda_con_productos(
        1, unidades=10
    )
    producto_id = productos[0]

    # Crear un pedido pagado de 3 unidades.
    pedido_id, _, _ = pedido_pagado(
        tienda_id, [(producto_id, 3)]
    )

    linea_id = lineas_de(pedido_id)[producto_id]["linea_id"]

    # Separar 2 unidades.
    respuesta = client.post(
        f"/v1/lineas-pedido/{linea_id}/separacion",
        headers={
            "Usuario-Id": str(PICKER),
            "Clave-Operacion": str(uuid4()),
        },
        json={"cantidad": 2},
    )

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["cantidad_separada"] == 2

    # Verificar los saldos de inventario.
    assert saldos_de(tienda_id, producto_id) == (
        8, 0, 1, 7
    )



def test_cl08_no_duplica_separacion():
    tienda_id, productos = tienda_con_productos(
        1, unidades=10
    )
    producto_id = productos[0]

    pedido_id, _, _ = pedido_pagado(
        tienda_id, [(producto_id, 3)]
    )

    linea_id = lineas_de(pedido_id)[producto_id]["linea_id"]

    clave = str(uuid4())
    headers = {
        "Usuario-Id": str(PICKER),
        "Clave-Operacion": clave,
    }

    url = f"/v1/lineas-pedido/{linea_id}/separacion"

    primera = client.post(
        url, headers=headers, json={"cantidad": 2}
    )

    segunda = client.post(
        url, headers=headers, json={"cantidad": 2}
    )

    assert primera.status_code == 200
    assert segunda.status_code == 200
    assert segunda.headers.get("Resultado-Previo") == "true"

    # La separación solo debe aplicarse una vez.
    assert lineas_de(pedido_id)[producto_id]["s"] == 2

    # Las existencias no deben descontarse dos veces.
    assert saldos_de(tienda_id, producto_id) == (
        8, 0, 1, 7
    )



def test_cl08_rechaza_cantidad_excesiva():
    tienda_id, productos = tienda_con_productos(
        1, unidades=10
    )
    producto_id = productos[0]

    pedido_id, _, _ = pedido_pagado(
        tienda_id, [(producto_id, 3)]
    )

    linea_id = lineas_de(pedido_id)[producto_id]["linea_id"]

    respuesta = client.post(
        f"/v1/lineas-pedido/{linea_id}/separacion",
        headers={
            "Usuario-Id": str(PICKER),
            "Clave-Operacion": str(uuid4()),
        },
        json={"cantidad": 4},
    )

    assert respuesta.status_code == 409
    assert respuesta.json()["codigo"] == "ESTADO_INCOMPATIBLE"

    # Ninguna unidad debe separarse.
    assert lineas_de(pedido_id)[producto_id]["s"] == 0
    assert saldos_de(tienda_id, producto_id) == (10, 0, 3, 7)



def test_cl08_rechaza_usuario_sin_permiso():
    tienda_id, productos = tienda_con_productos(
        1, unidades=10
    )
    producto_id = productos[0]

    pedido_id, _, _ = pedido_pagado(
        tienda_id, [(producto_id, 3)]
    )

    linea_id = lineas_de(pedido_id)[producto_id]["linea_id"]

    # Crear un usuario existente, pero sin rol en la tienda.
    usuario_sin_permiso = uuid4()

    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO usuario_operativo (usuario_id, nombre)
            VALUES (%s, %s)
            """,
            (usuario_sin_permiso, "Usuario sin permiso"),
        )

    respuesta = client.post(
        f"/v1/lineas-pedido/{linea_id}/separacion",
        headers={
            "Usuario-Id": str(usuario_sin_permiso),
            "Clave-Operacion": str(uuid4()),
        },
        json={"cantidad": 2},
    )

    assert respuesta.status_code == 403
    assert respuesta.json()["codigo"] == "SIN_PERMISO"

    # La operación rechazada no modifica las existencias.
    assert lineas_de(pedido_id)[producto_id]["s"] == 0
    assert saldos_de(tienda_id, producto_id) == (10, 0, 3, 7)



def test_cl08_rechaza_usuario_inexistente():
    tienda_id, productos = tienda_con_productos(
        1, unidades=10
    )
    producto_id = productos[0]

    pedido_id, _, _ = pedido_pagado(
        tienda_id, [(producto_id, 3)]
    )

    linea_id = lineas_de(pedido_id)[producto_id]["linea_id"]

    # UUID de un usuario que no existe en la base de datos.
    usuario_inexistente = uuid4()

    respuesta = client.post(
        f"/v1/lineas-pedido/{linea_id}/separacion",
        headers={
            "Usuario-Id": str(usuario_inexistente),
            "Clave-Operacion": str(uuid4()),
        },
        json={"cantidad": 2},
    )

    assert respuesta.status_code == 403
    assert respuesta.json()["codigo"] == "SIN_PERMISO"

    # Comprobar que el pedido y el inventario no cambiaron.
    assert lineas_de(pedido_id)[producto_id]["s"] == 0
    assert saldos_de(tienda_id, producto_id) == (10, 0, 3, 7)



def test_cl08_separacion_completa_en_dos_operaciones():
    tienda_id, productos = tienda_con_productos(
        1, unidades=10
    )
    producto_id = productos[0]

    pedido_id, _, _ = pedido_pagado(
        tienda_id, [(producto_id, 3)]
    )

    linea_id = lineas_de(pedido_id)[producto_id]["linea_id"]
    url = f"/v1/lineas-pedido/{linea_id}/separacion"

    # Primera operación: separar 2 de 3 unidades.
    primera = client.post(
        url,
        headers={
            "Usuario-Id": str(PICKER),
            "Clave-Operacion": str(uuid4()),
        },
        json={"cantidad": 2},
    )

    assert primera.status_code == 200
    assert primera.json()["estado"] == "ASIGNADA"

    # Segunda operación: separar la unidad restante.
    segunda = client.post(
        url,
        headers={
            "Usuario-Id": str(PICKER),
            "Clave-Operacion": str(uuid4()),
        },
        json={"cantidad": 1},
    )

    assert segunda.status_code == 200
    assert segunda.json()["cantidad_separada"] == 3
    assert segunda.json()["estado"] == "SEPARADA"

    # Comprobar el estado definitivo.
    linea = lineas_de(pedido_id)[producto_id]

    assert linea["s"] == 3
    assert linea["estado"] == "SEPARADA"

    # Inventario físico = 7, comprometido = 0.
    assert saldos_de(tienda_id, producto_id) == (
        7, 0, 0, 7
    )



def test_cl08_concurrencia_no_sobresepara():
    tienda_id, productos = tienda_con_productos(
        1, unidades=10
    )
    producto_id = productos[0]

    pedido_id, _, _ = pedido_pagado(
        tienda_id, [(producto_id, 3)]
    )

    linea_id = lineas_de(pedido_id)[producto_id]["linea_id"]

    url = f"/v1/lineas-pedido/{linea_id}/separacion"

    # Coordinar dos solicitudes simultáneas.
    barrera = Barrier(2)

    def intentar_separar():
        barrera.wait(timeout=10)

        return TestClient(app).post(
            url,
            headers={
                "Usuario-Id": str(PICKER),
                "Clave-Operacion": str(uuid4()),
            },
            json={"cantidad": 2},
        )

    # Ejecutar ambas solicitudes en paralelo.
    with ThreadPoolExecutor(max_workers=2) as executor:
        futuros = [
            executor.submit(intentar_separar)
            for _ in range(2)
        ]
        respuestas = [f.result() for f in futuros]

    # Una solicitud aprobada y otra rechazada.
    codigos = sorted(r.status_code for r in respuestas)
    assert codigos == [200, 409]

    rechazada = next(
        r for r in respuestas if r.status_code == 409
    )
    assert rechazada.json()["codigo"] == "ESTADO_INCOMPATIBLE"

    # Solo se separaron 2 unidades, no 4.
    linea = lineas_de(pedido_id)[producto_id]
    assert linea["s"] == 2

    # El inventario conserva saldos correctos.
    assert saldos_de(tienda_id, producto_id) == (
        8, 0, 1, 7
    )



def test_cl08_rechaza_cantidad_cero():
    tienda_id, productos = tienda_con_productos(
        1, unidades=10
    )
    producto_id = productos[0]

    pedido_id, _, _ = pedido_pagado(
        tienda_id, [(producto_id, 3)]
    )

    linea_id = lineas_de(pedido_id)[producto_id]["linea_id"]

    respuesta = client.post(
        f"/v1/lineas-pedido/{linea_id}/separacion",
        headers={
            "Usuario-Id": str(PICKER),
            "Clave-Operacion": str(uuid4()),
        },
        json={"cantidad": 0},
    )

    assert respuesta.status_code == 400
    assert respuesta.json()["codigo"] == "SOLICITUD_INVALIDA"

    # La operación rechazada no modifica inventario.
    assert lineas_de(pedido_id)[producto_id]["s"] == 0
    assert saldos_de(tienda_id, producto_id) == (10, 0, 3, 7)



def test_cl08_registra_movimiento_auditable():
    tienda_id, productos = tienda_con_productos(
        1, unidades=10
    )
    producto_id = productos[0]

    pedido_id, _, _ = pedido_pagado(
        tienda_id, [(producto_id, 3)]
    )
    linea_id = lineas_de(pedido_id)[producto_id]["linea_id"]
    clave = uuid4()

    respuesta = client.post(
        f"/v1/lineas-pedido/{linea_id}/separacion",
        headers={
            "Usuario-Id": str(PICKER),
            "Clave-Operacion": str(clave),
        },
        json={"cantidad": 2},
    )

    assert respuesta.status_code == 200, respuesta.text

    with pool.connection() as conn:
        movimientos = conn.execute(
            """
            SELECT m.movimiento_id, m.usuario_id,
                   m.clave_operacion, ms.saldo, ms.cantidad
            FROM movimiento_inventario m
            JOIN movimiento_saldo ms
              ON ms.movimiento_id = m.movimiento_id
            WHERE m.referencia_linea_id = %s
              AND m.tipo = 'SEPARACION'
            ORDER BY ms.saldo
            """,
            (linea_id,),
        ).fetchall()

    # Un movimiento con dos registros de saldo.
    assert len(movimientos) == 2
    assert len({fila[0] for fila in movimientos}) == 1

    # Verificar usuario y clave.
    assert all(
        fila[1] == PICKER and fila[2] == clave
        for fila in movimientos
    )

    # Se descuentan 2 unidades de E y C.
    assert [(fila[3], fila[4]) for fila in movimientos] == [
        ("C", -2),
        ("E", -2),
    ]


def test_cl08_responde_en_la_ruta_del_contrato_openapi():
    from fastapi.testclient import TestClient

    from app.main import app

    rutas = {r.path for r in app.routes}
    assert "/v1/lineas-pedido/{linea_id}/separaciones" in rutas
    assert "/v1/lineas-pedido/{linea_id}/separaciones" in TestClient(app).get("/openapi.json").json()["paths"]
