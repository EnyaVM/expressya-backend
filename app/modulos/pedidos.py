from decimal import Decimal
from uuid import UUID, uuid4

from fastapi import APIRouter

from app.db import pool
from app.modulos.reservas import cerrar_si_sigue_activa

router = APIRouter(tags=["Pedidos (M03)"])


def _disparar_reembolso_interno(
    conn,
    intento_id: UUID,
    monto: Decimal,
    motivo: str,
) -> UUID | None:
    """Importación diferida para evitar ciclos entre M03 y M04 al iniciar."""
    from app.modulos.pagos import solicitar_reembolso

    return solicitar_reembolso(conn, intento_id=intento_id, monto=monto, motivo=motivo)


def confirmar_compra(conn, intento_id: UUID) -> UUID | None:
    """
    CL-07 (interno M03): Confirma la compra ante un EV-01 de intento APROBADO.
    Orden de bloqueo: Pedido -> Reserva y líneas -> Inventario (producto_id ASC)
    -> LineaPedido -> IntentoPago -> Reembolso.
    Devuelve el reembolso_id si se disparó reembolso automático (Caso B o duplicado), o None.
    """
    with conn.cursor() as cur:
        # 1. Obtener datos básicos del intento para localizar el pedido
        cur.execute(
            """
            SELECT pedido_id, monto, estado
            FROM intento_pago
            WHERE intento_id = %s
            """,
            (intento_id,),
        )
        fila_intento = cur.fetchone()
        if fila_intento is None:
            return None
        pedido_id, monto_intento, estado_intento = fila_intento

        # 2. Bloquear el pedido (primer bloqueo obligatorio)
        cur.execute(
            """
            SELECT pedido_id, reserva_id, darkstore_id, estado, intento_aplicado_id
            FROM pedido
            WHERE pedido_id = %s
            FOR UPDATE
            """,
            (pedido_id,),
        )
        fila_pedido = cur.fetchone()
        if fila_pedido is None:
            return None
        _, reserva_id, darkstore_id, estado_pedido, intento_aplicado_id = fila_pedido

        # Bloquear reserva y líneas en orden determinista
        cur.execute(
            """
            SELECT reserva_id, estado, t_vence
            FROM reserva
            WHERE reserva_id = %s
            FOR UPDATE
            """,
            (reserva_id,),
        )
        fila_reserva = cur.fetchone()
        estado_reserva = fila_reserva[1] if fila_reserva else None

        cur.execute(
            """
            SELECT producto_id, cantidad, precio_unitario
            FROM linea_reserva
            WHERE reserva_id = %s AND fecha_retiro IS NULL
            ORDER BY producto_id ASC
            FOR UPDATE
            """,
            (reserva_id,),
        )
        lineas_reserva = cur.fetchall()

        # Bloquear filas de inventario por producto_id ascendente
        if lineas_reserva:
            productos_ids = [linea[0] for linea in lineas_reserva]
            cur.execute(
                """
                SELECT producto_id
                FROM inventario
                WHERE darkstore_id = %s AND producto_id = ANY(%s)
                ORDER BY producto_id ASC
                FOR UPDATE
                """,
                (darkstore_id, productos_ids),
            )

        # Bloquear intento_pago al final del orden
        cur.execute(
            """
            SELECT estado
            FROM intento_pago
            WHERE intento_id = %s
            FOR UPDATE
            """,
            (intento_id,),
        )
        estado_intento = cur.fetchone()[0]

        # Idempotencia de evento repetido: si este intento ya fue aplicado o ya tiene reembolso iniciado
        if intento_aplicado_id == intento_id or estado_intento != "APROBADO":
            return None

        # 3. Cobro duplicado (ADV-001): el pedido ya está CONFIRMADO con otro intento aplicado
        if estado_pedido == "CONFIRMADO":
            return _disparar_reembolso_interno(
                conn,
                intento_id=intento_id,
                monto=monto_intento,
                motivo="COBRO_DUPLICADO",
            )

        # 4. Caso A (reloj < t_vence y reserva ACTIVA): comprometer reserva y confirmar pedido
        cur.execute(
            """
            UPDATE reserva
            SET estado = 'COMPROMETIDA',
                motivo_cierre = 'PAGO_CONFIRMADO',
                fecha_cierre = clock_timestamp()
            WHERE reserva_id = %s
              AND estado = 'ACTIVA'
              AND clock_timestamp() < t_vence
            RETURNING reserva_id
            """,
            (reserva_id,),
        )
        comprometida = cur.fetchone()

        if comprometida is not None:
            for producto_id, cantidad, precio_unitario in lineas_reserva:
                cur.execute(
                    """
                    INSERT INTO linea_pedido (
                        linea_id, pedido_id, reserva_id, producto_id, darkstore_id,
                        precio_unitario, q, s, k, fecha_compromiso, estado
                    )
                    VALUES (
                        gen_random_uuid(), %s, %s, %s, %s,
                        %s, %s, 0, 0, clock_timestamp(), 'ASIGNADA'
                    )
                    """,
                    (
                        pedido_id,
                        reserva_id,
                        producto_id,
                        darkstore_id,
                        precio_unitario,
                        cantidad,
                    ),
                )

                movimiento_id = uuid4()
                cur.execute(
                    """
                    INSERT INTO movimiento_inventario (
                        movimiento_id, darkstore_id, producto_id, tipo, fecha,
                        tx_origen, motivo, clave_operacion, referencia_reserva_id
                    )
                    VALUES (
                        %s, %s, %s, 'COMPROMISO', clock_timestamp(),
                        %s, 'Compromiso de inventario por pago aprobado', %s, %s
                    )
                    """,
                    (
                        movimiento_id,
                        darkstore_id,
                        producto_id,
                        None,  # tx_origen como NULL (bigint)
                        uuid4(),
                        reserva_id,
                    ),
                )

                cur.execute(
                    """
                    INSERT INTO movimiento_saldo (movimiento_id, saldo, cantidad)
                    VALUES
                        (%s, 'R', %s),
                        (%s, 'C', %s)
                    """,
                    (
                        movimiento_id,
                        -cantidad,
                        movimiento_id,
                        cantidad,
                    ),
                )

            cur.execute(
                """
                UPDATE pedido
                SET estado = 'CONFIRMADO',
                    intento_aplicado_id = %s
                WHERE pedido_id = %s
                """,
                (intento_id, pedido_id),
            )
            return None

        # 5. Caso B (reloj >= t_vence o reserva ya EXPIRADA/LIBERADA): pago tardío -> reembolso 100%
        if estado_reserva == "ACTIVA":
            cerrar_si_sigue_activa(
                conn,
                reserva_id,
                "EXPIRADA",
                "VENCIMIENTO",
                "EXPIRACION",
                "Vencimiento de la reserva (300 s)",
                solo_si_vencida=True,
            )

        cur.execute(
            """
            UPDATE pedido
            SET estado = 'SIN_COMPRA',
                ajuste_pendiente = true
            WHERE pedido_id = %s
            """,
            (pedido_id,),
        )

        return _disparar_reembolso_interno(
            conn,
            intento_id=intento_id,
            monto=monto_intento,
            motivo="PAGO_TARDIO",
        )


def procesar_eventos_pendientes(limite: int = 50) -> int:
    """
    Consumidor de eventos EV-01. Procesa hasta `limite` eventos en transacciones individuales
    con FOR UPDATE SKIP LOCKED. Envía los reembolsos pendientes fuera de la transacción.
    """
    from app.modulos.pagos import enviar_reembolso

    procesados = 0
    for _ in range(limite):
        evento_id: UUID | None = None
        intento_id: UUID | None = None
        reembolso_a_enviar: UUID | None = None

        with pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT evento_id, intento_id
                        FROM evento_interno
                        WHERE NOT procesado AND tipo = 'EV-01'
                        ORDER BY fecha ASC
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                        """
                    )
                    fila = cur.fetchone()
                    if fila is None:
                        break
                    evento_id, intento_id = fila

            try:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            SELECT evento_id
                            FROM evento_interno
                            WHERE evento_id = %s AND NOT procesado
                            FOR UPDATE
                            """,
                            (evento_id,),
                        )
                        if cur.fetchone() is None:
                            continue

                        reembolso_a_enviar = confirmar_compra(conn, intento_id)

                        cur.execute(
                            """
                            UPDATE evento_interno
                            SET procesado = true,
                                procesado_en = clock_timestamp(),
                                intentos_procesamiento = intentos_procesamiento + 1
                            WHERE evento_id = %s
                            """,
                            (evento_id,),
                        )
                        procesados += 1
            except Exception:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE evento_interno
                            SET intentos_procesamiento = intentos_procesamiento + 1
                            WHERE evento_id = %s
                            """,
                            (evento_id,),
                        )
                continue

        if reembolso_a_enviar is not None:
            enviar_reembolso(reembolso_a_enviar)

    return procesados