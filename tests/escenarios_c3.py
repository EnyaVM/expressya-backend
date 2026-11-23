from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.db import pool
from app.main import app
from escenarios import PICKER, nuevo_cliente

client = TestClient(app)


def tienda_con_productos(cantidad_productos: int, unidades: int = 10, precio: str = "100.00"):
    darkstore_id = uuid4()
    productos = [uuid4() for _ in range(cantidad_productos)]
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO dark_store (darkstore_id, nombre, ubicacion) VALUES (%s, 'Tienda C3', 'Prueba')",
            (darkstore_id,),
        )
        conn.execute(
            "INSERT INTO asignacion_rol (usuario_id, darkstore_id, rol) VALUES (%s, %s, 'PICKER')",
            (PICKER, darkstore_id),
        )
        for producto_id in productos:
            conn.execute(
                "INSERT INTO producto (producto_id, nombre, precio_unitario) VALUES (%s, 'Prenda C3', %s)",
                (producto_id, precio),
            )
            conn.execute(
                "INSERT INTO inventario (darkstore_id, producto_id) VALUES (%s, %s)",
                (darkstore_id, producto_id),
            )
            with conn.transaction():
                movimiento_id = conn.execute(
                    "INSERT INTO movimiento_inventario (darkstore_id, producto_id, tipo, motivo, usuario_id, clave_operacion) "
                    "VALUES (%s, %s, 'AJUSTE_INICIAL', 'escenario C3', %s, %s) RETURNING movimiento_id",
                    (darkstore_id, producto_id, PICKER, uuid4()),
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO movimiento_saldo (movimiento_id, saldo, cantidad) VALUES (%s, 'E', %s)",
                    (movimiento_id, unidades),
                )
                conn.execute(
                    "UPDATE inventario SET e = %s WHERE darkstore_id = %s AND producto_id = %s",
                    (unidades, darkstore_id, producto_id),
                )
    return darkstore_id, productos


def pedido_pagado(darkstore_id: UUID, lineas: list[tuple[UUID, int]], cliente_id: UUID | None = None):
    cliente_id = cliente_id or nuevo_cliente()
    checkout = client.post(
        "/v1/checkouts",
        headers={"Clave-Operacion": str(uuid4()), "Cliente-Id": str(cliente_id)},
        json={
            "darkstore_id": str(darkstore_id),
            "lineas": [{"producto_id": str(p), "cantidad": c} for p, c in lineas],
        },
    )
    assert checkout.status_code == 201, checkout.text
    pedido = checkout.json()["pedido"]
    pago = client.post(
        f"/v1/pedidos/{pedido['pedido_id']}/intentos-pago",
        headers={"Clave-Operacion": str(uuid4()), "Cliente-Id": str(cliente_id)},
        json={"monto": f"{float(pedido['total_vigente']):.2f}"},
    )
    assert pago.status_code == 201, pago.text
    assert pago.json()["estado"] == "APROBADO"
    pedido_id = UUID(pedido["pedido_id"])
    assert estado_pedido(pedido_id) == "CONFIRMADO"
    return pedido_id, cliente_id, UUID(pago.json()["intento_id"])


def cancelar(pedido_id: UUID, cliente_id: UUID, clave: UUID | None = None, motivo: str = "Ya no lo necesito"):
    return client.post(
        f"/v1/pedidos/{pedido_id}/cancelacion",
        headers={"Clave-Operacion": str(clave or uuid4()), "Cliente-Id": str(cliente_id)},
        json={"motivo": motivo},
    )


def estado_pedido(pedido_id: UUID) -> str:
    with pool.connection() as conn:
        return conn.execute("SELECT estado FROM pedido WHERE pedido_id = %s", (pedido_id,)).fetchone()[0]


def lineas_de(pedido_id: UUID) -> dict:
    with pool.connection() as conn:
        filas = conn.execute(
            "SELECT producto_id, linea_id, q, s, k, estado FROM linea_pedido WHERE pedido_id = %s",
            (pedido_id,),
        ).fetchall()
    return {f[0]: {"linea_id": f[1], "q": f[2], "s": f[3], "k": f[4], "estado": f[5]} for f in filas}


def saldos_de(darkstore_id: UUID, producto_id: UUID):
    with pool.connection() as conn:
        return conn.execute(
            "SELECT e, r, c, d FROM v_saldos_inventario WHERE darkstore_id = %s AND producto_id = %s",
            (darkstore_id, producto_id),
        ).fetchone()


def reembolsos_de(intento_id: UUID):
    with pool.connection() as conn:
        return conn.execute(
            "SELECT reembolso_id, monto, motivo, cancelacion_id, fecha_confirmacion FROM reembolso "
            "WHERE intento_id = %s ORDER BY fecha_solicitud",
            (intento_id,),
        ).fetchall()


def estado_intento(intento_id: UUID) -> str:
    with pool.connection() as conn:
        return conn.execute("SELECT estado FROM intento_pago WHERE intento_id = %s", (intento_id,)).fetchone()[0]


def crear_resolucion_faltante(darkstore_id: UUID, producto_id: UUID, linea_id: UUID, canceladas: int) -> UUID:
    with pool.connection() as conn:
        with conn.transaction():
            e_actual = conn.execute(
                "SELECT e FROM inventario WHERE darkstore_id = %s AND producto_id = %s",
                (darkstore_id, producto_id),
            ).fetchone()[0]
            conteo_id = conn.execute(
                "INSERT INTO conteo_fisico (darkstore_id, producto_id, cantidad_contada, e_al_corte, fecha_corte, usuario_id) "
                "VALUES (%s, %s, %s, %s, clock_timestamp(), %s) RETURNING conteo_id",
                (darkstore_id, producto_id, max(e_actual - canceladas, 0), e_actual, PICKER),
            ).fetchone()[0]
            discrepancia_id = conn.execute(
                "INSERT INTO discrepancia (darkstore_id, producto_id, tipo, origen, conteo_origen_id) "
                "VALUES (%s, %s, 'FALTANTE', 'CONTEO', %s) RETURNING discrepancia_id",
                (darkstore_id, producto_id, conteo_id),
            ).fetchone()[0]
            resolucion_id = conn.execute(
                "INSERT INTO resolucion_faltante (discrepancia_id, darkstore_id, producto_id, supervisor_id, "
                "unidades_validadas, conteo_id, evidencia, regla_aplicada, motivo) "
                "VALUES (%s, %s, %s, %s, %s, %s, 'conteo de prueba', 'FIFO por fecha_compromiso', 'faltante validado') "
                "RETURNING resolucion_id",
                (discrepancia_id, darkstore_id, producto_id, PICKER, canceladas, conteo_id),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO asignacion_faltante (resolucion_id, linea_id, darkstore_id, producto_id, "
                "separadas_antes, asignadas, canceladas) VALUES (%s, %s, %s, %s, 0, 0, %s)",
                (resolucion_id, linea_id, darkstore_id, producto_id, canceladas),
            )
    return resolucion_id


def aplicar_faltante(resolucion_id: UUID, linea_id: UUID):
    from app.modulos.cancelaciones import cancelar_cantidad_por_faltante
    from app.modulos.pagos import enviar_reembolso

    with pool.connection() as conn:
        with conn.transaction():
            reembolso = cancelar_cantidad_por_faltante(conn, resolucion_id, linea_id, PICKER, uuid4())
    if reembolso is not None:
        enviar_reembolso(reembolso)
    return reembolso
