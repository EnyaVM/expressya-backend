from uuid import UUID, uuid4

from app.db import pool

PICKER = UUID("44444444-4444-4444-4444-444444444444")


def preparar_escenario(unidades: int, precio: str = "100.00"):
    darkstore_id = uuid4()
    producto_id = uuid4()
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO dark_store (darkstore_id, nombre, ubicacion) VALUES (%s, 'Tienda de prueba', 'Prueba')",
            (darkstore_id,),
        )
        conn.execute(
            "INSERT INTO producto (producto_id, nombre, precio_unitario) VALUES (%s, 'Producto de prueba', %s)",
            (producto_id, precio),
        )
        conn.execute(
            "INSERT INTO asignacion_rol (usuario_id, darkstore_id, rol) VALUES (%s, %s, 'PICKER')",
            (PICKER, darkstore_id),
        )
        conn.execute(
            "INSERT INTO inventario (darkstore_id, producto_id) VALUES (%s, %s)",
            (darkstore_id, producto_id),
        )
        movimiento_id = conn.execute(
            "INSERT INTO movimiento_inventario (darkstore_id, producto_id, tipo, motivo, usuario_id, clave_operacion) "
            "VALUES (%s, %s, 'AJUSTE_INICIAL', 'escenario de prueba', %s, %s) RETURNING movimiento_id",
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
    return darkstore_id, producto_id


def nuevo_cliente():
    cliente_id = uuid4()
    with pool.connection() as conn:
        conn.execute("INSERT INTO cliente (cliente_id) VALUES (%s)", (cliente_id,))
    return cliente_id


def saldos(darkstore_id: UUID, producto_id: UUID):
    with pool.connection() as conn:
        return conn.execute(
            "SELECT e, r, d FROM v_saldos_inventario WHERE darkstore_id = %s AND producto_id = %s",
            (darkstore_id, producto_id),
        ).fetchone()


def crear_reserva_vencida(cliente_id: UUID, darkstore_id: UUID, producto_id: UUID, cantidad: int = 1):
    pedido_id = uuid4()
    reserva_id = uuid4()
    with pool.connection() as conn:
        creada = conn.execute("SELECT clock_timestamp() - INTERVAL '301 seconds'").fetchone()[0]
        conn.execute("SET LOCAL session_replication_role = replica")
        conn.execute(
            "INSERT INTO pedido (pedido_id, reserva_id, cliente_id, darkstore_id, total_vigente) "
            "VALUES (%s, %s, %s, %s, 100.00)",
            (pedido_id, reserva_id, cliente_id, darkstore_id),
        )
        conn.execute(
            "INSERT INTO reserva (reserva_id, pedido_id, cliente_id, darkstore_id, t_creacion, t_vence) "
            "VALUES (%s, %s, %s, %s, %s, %s + INTERVAL '300 seconds')",
            (reserva_id, pedido_id, cliente_id, darkstore_id, creada, creada),
        )
        conn.execute(
            "INSERT INTO linea_reserva (reserva_id, producto_id, darkstore_id, cantidad, precio_unitario) "
            "VALUES (%s, %s, %s, %s, 100.00)",
            (reserva_id, producto_id, darkstore_id, cantidad),
        )
    with pool.connection() as conn:
        movimiento_id = conn.execute(
            "INSERT INTO movimiento_inventario "
            "(darkstore_id, producto_id, tipo, motivo, clave_operacion, referencia_reserva_id) "
            "VALUES (%s, %s, 'RESERVA', 'Reserva de checkout', %s, %s) RETURNING movimiento_id",
            (darkstore_id, producto_id, uuid4(), reserva_id),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO movimiento_saldo (movimiento_id, saldo, cantidad) VALUES (%s, 'R', %s)",
            (movimiento_id, cantidad),
        )
    return pedido_id, reserva_id


def estados(reserva_id: UUID):
    with pool.connection() as conn:
        return conn.execute(
            "SELECT r.estado, r.motivo_cierre, p.estado FROM reserva r JOIN pedido p USING (pedido_id) "
            "WHERE r.reserva_id = %s",
            (reserva_id,),
        ).fetchone()


def movimientos_de(reserva_id: UUID):
    with pool.connection() as conn:
        return conn.execute(
            "SELECT m.tipo, ms.saldo, ms.cantidad FROM movimiento_inventario m "
            "JOIN movimiento_saldo ms USING (movimiento_id) "
            "WHERE m.referencia_reserva_id = %s ORDER BY m.fecha, m.tipo",
            (reserva_id,),
        ).fetchall()


def reconstruir_saldos(darkstore_id: UUID, producto_id: UUID):
    with pool.connection() as conn:
        sumas = dict(
            conn.execute(
                "SELECT ms.saldo, sum(ms.cantidad)::int FROM movimiento_inventario m "
                "JOIN movimiento_saldo ms USING (movimiento_id) "
                "WHERE m.darkstore_id = %s AND m.producto_id = %s GROUP BY ms.saldo",
                (darkstore_id, producto_id),
            ).fetchall()
        )
    return sumas.get("E", 0), sumas.get("R", 0)

