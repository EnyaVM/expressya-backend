from decimal import Decimal
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, Response
from pydantic import BaseModel, Field

from app.db import pool
from app.errores import ErrorDeNegocio
from app.idempotencia import calcular_huella, ejecutar_idempotente, validar_clave_operacion
from app.modulos.avisos import registrar_aviso

router = APIRouter(tags=["Cancelaciones (M03)"])


class CancelarPedidoSolicitud(BaseModel):
    motivo: str = Field(min_length=1, max_length=200)


def _parsear_cliente(valor: str | None) -> UUID:
    if not valor:
        raise ErrorDeNegocio(401, "NO_AUTENTICADO", "Falta la cabecera obligatoria Cliente-Id.")
    try:
        return UUID(valor.strip())
    except ValueError as exc:
        raise ErrorDeNegocio(400, "SOLICITUD_INVALIDA", "La cabecera Cliente-Id debe ser un UUID válido.") from exc


def _bloquear_inventario(cur, darkstore_id: UUID, productos: list[UUID]) -> None:
    cur.execute(
        """
        SELECT producto_id
        FROM inventario
        WHERE darkstore_id = %s AND producto_id = ANY(%s)
        ORDER BY producto_id ASC
        FOR UPDATE
        """,
        (darkstore_id, productos),
    )


def _registrar_movimiento_cancelacion(
    cur,
    darkstore_id: UUID,
    producto_id: UUID,
    cantidad: int,
    motivo: str,
    clave_operacion: UUID,
    reserva_id: UUID,
    linea_id: UUID,
    usuario_id: UUID | None = None,
    resolucion_id: UUID | None = None,
) -> None:
    cur.execute(
        """
        INSERT INTO movimiento_inventario (
            darkstore_id, producto_id, tipo, motivo, usuario_id, clave_operacion,
            referencia_reserva_id, referencia_linea_id, referencia_resolucion_id
        )
        VALUES (%s, %s, 'CANCELACION_LINEA', %s, %s, %s, %s, %s, %s)
        RETURNING movimiento_id
        """,
        (darkstore_id, producto_id, motivo, usuario_id, clave_operacion, reserva_id, linea_id, resolucion_id),
    )
    movimiento_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO movimiento_saldo (movimiento_id, saldo, cantidad) VALUES (%s, 'C', %s)",
        (movimiento_id, -cantidad),
    )


def _reembolso_total_de(cur, intento_id: UUID) -> UUID:
    cur.execute(
        """
        SELECT reembolso_id
        FROM reembolso
        WHERE intento_id = %s AND cancelacion_id IS NULL AND reserva_origen_id IS NULL
        """,
        (intento_id,),
    )
    return cur.fetchone()[0]


def cancelar_pedido_cliente(
    conn,
    pedido_id: UUID,
    cliente_id: UUID,
    motivo: str,
    clave_operacion: UUID,
) -> tuple[dict, UUID | None]:
    from app.modulos.pagos import solicitar_reembolso

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT reserva_id, cliente_id, darkstore_id, estado, total_vigente, intento_aplicado_id
            FROM pedido
            WHERE pedido_id = %s
            FOR UPDATE
            """,
            (pedido_id,),
        )
        fila = cur.fetchone()
        if fila is None or fila[1] != cliente_id:
            raise ErrorDeNegocio(404, "NO_ENCONTRADO", "El pedido no existe o no pertenece al cliente.")
        reserva_id, _, darkstore_id, estado, total_vigente, intento_id = fila
        if estado != "CONFIRMADO":
            raise ErrorDeNegocio(
                409,
                "ESTADO_INCOMPATIBLE",
                f"El pedido está en estado {estado} y no admite cancelación.",
            )

        cur.execute(
            "SELECT producto_id FROM linea_pedido WHERE pedido_id = %s ORDER BY producto_id",
            (pedido_id,),
        )
        _bloquear_inventario(cur, darkstore_id, [f[0] for f in cur.fetchall()])

        cur.execute(
            """
            SELECT linea_id, producto_id, q, s, k, estado
            FROM linea_pedido
            WHERE pedido_id = %s
            ORDER BY linea_id
            FOR UPDATE
            """,
            (pedido_id,),
        )
        lineas = [fila[:5] for fila in cur.fetchall()]
        if any(s > 0 for _, _, _, s, _ in lineas):
            raise ErrorDeNegocio(
                409,
                "PREPARACION_INICIADA",
                "La preparación del pedido ya inició; el cliente ya no puede cancelarlo.",
            )
        cur.execute(
            "SELECT count(*) FROM linea_pedido WHERE pedido_id = %s AND estado NOT IN ('ASIGNADA', 'CANCELADA')",
            (pedido_id,),
        )
        if cur.fetchone()[0] > 0:
            raise ErrorDeNegocio(
                409,
                "ESTADO_INCOMPATIBLE",
                "El pedido tiene una línea con un faltante en validación; se resuelve con D-05.",
            )

        for linea_id, producto_id, q, s, k in lineas:
            pendiente = q - s - k
            if pendiente == 0:
                continue
            cur.execute(
                """
                UPDATE linea_pedido SET k = q, estado = 'CANCELADA'
                WHERE linea_id = %s AND estado = 'ASIGNADA' AND s + k + %s <= q
                """,
                (linea_id, pendiente),
            )
            if cur.rowcount != 1:
                raise ErrorDeNegocio(409, "ESTADO_INCOMPATIBLE", "La línea cambió de estado; intenta de nuevo.")
            _registrar_movimiento_cancelacion(
                cur,
                darkstore_id,
                producto_id,
                pendiente,
                f"Cancelación del cliente: {motivo}",
                clave_operacion,
                reserva_id,
                linea_id,
            )

        cur.execute(
            "UPDATE pedido SET estado = 'CANCELADO', total_vigente = 0 WHERE pedido_id = %s",
            (pedido_id,),
        )

        reembolso_a_enviar = None
        reembolso_id = None
        if total_vigente > 0:
            reembolso_a_enviar = solicitar_reembolso(conn, intento_id, total_vigente, "CANCELACION_CLIENTE")
            reembolso_id = _reembolso_total_de(cur, intento_id)

        registrar_aviso(
            conn,
            tipo="PEDIDO_CANCELADO",
            origen_tipo="PEDIDO",
            origen_id=pedido_id,
            cliente_id=cliente_id,
            pedido_id=pedido_id,
        )

    cuerpo = {
        "pedido_id": str(pedido_id),
        "estado": "CANCELADO",
        "total_vigente": "0.00",
        "ajuste_pendiente": reembolso_id is not None,
        "reembolso_id": str(reembolso_id) if reembolso_id else None,
        "monto_reembolso": f"{total_vigente:.2f}",
    }
    resultado = {
        "estado_http": 200,
        "cuerpo": cuerpo,
        "estado_anterior": {"pedido": "CONFIRMADO", "total_vigente": f"{total_vigente:.2f}"},
        "estado_resultante": {"pedido": "CANCELADO", "total_vigente": "0.00"},
    }
    return resultado, reembolso_a_enviar


def cancelar_cantidad_por_faltante(
    conn,
    resolucion_id: UUID,
    linea_id: UUID,
    usuario_id: UUID,
    clave_operacion: UUID,
) -> UUID | None:
    from app.modulos.pagos import solicitar_reembolso

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.canceladas, lp.pedido_id
            FROM asignacion_faltante a
            JOIN linea_pedido lp USING (linea_id)
            WHERE a.resolucion_id = %s AND a.linea_id = %s
            """,
            (resolucion_id, linea_id),
        )
        fila = cur.fetchone()
        if fila is None:
            raise ErrorDeNegocio(404, "NO_ENCONTRADO", "No existe la asignación del faltante para esa línea.")
        cantidad, pedido_id = fila
        if cantidad == 0:
            return None

        cur.execute(
            """
            SELECT reserva_id, cliente_id, darkstore_id, estado, total_vigente, intento_aplicado_id
            FROM pedido
            WHERE pedido_id = %s
            FOR UPDATE
            """,
            (pedido_id,),
        )
        reserva_id, cliente_id, darkstore_id, estado, total_vigente, intento_id = cur.fetchone()

        cur.execute(
            "SELECT 1 FROM cancelacion_linea WHERE resolucion_id = %s AND linea_id = %s",
            (resolucion_id, linea_id),
        )
        if cur.fetchone() is not None:
            return None
        if estado != "CONFIRMADO":
            raise ErrorDeNegocio(
                409,
                "ESTADO_INCOMPATIBLE",
                f"El pedido está en estado {estado} y no admite cancelación por faltante.",
            )

        cur.execute(
            "SELECT producto_id FROM linea_pedido WHERE linea_id = %s",
            (linea_id,),
        )
        producto_id = cur.fetchone()[0]
        _bloquear_inventario(cur, darkstore_id, [producto_id])

        cur.execute(
            "SELECT q, s, k, precio_unitario, estado FROM linea_pedido WHERE linea_id = %s FOR UPDATE",
            (linea_id,),
        )
        q, s, k, precio_unitario, estado_linea = cur.fetchone()
        if cantidad > q - s - k:
            raise ErrorDeNegocio(
                409,
                "ESTADO_INCOMPATIBLE",
                "La línea no tiene suficientes unidades pendientes para cancelar.",
            )

        nuevo_k = k + cantidad
        if s + nuevo_k == q:
            nuevo_estado_linea = "CANCELADA" if s == 0 else "PARCIAL"
        else:
            nuevo_estado_linea = estado_linea
        cur.execute(
            "UPDATE linea_pedido SET k = %s, estado = %s WHERE linea_id = %s",
            (nuevo_k, nuevo_estado_linea, linea_id),
        )

        monto = (Decimal(precio_unitario) * cantidad).quantize(Decimal("0.01"))
        cancelacion_id = uuid4()
        reembolso_a_enviar = solicitar_reembolso(
            conn,
            intento_id,
            monto,
            "CANCELACION_POR_FALTANTE",
            cancelacion_id=cancelacion_id,
        )
        cur.execute(
            "SELECT reembolso_id FROM reembolso WHERE cancelacion_id = %s",
            (cancelacion_id,),
        )
        reembolso_id = cur.fetchone()[0]

        cur.execute(
            """
            INSERT INTO cancelacion_linea (
                cancelacion_id, linea_id, pedido_id, cantidad, causa,
                resolucion_id, usuario_id, reembolso_id
            )
            VALUES (%s, %s, %s, %s, 'FALTANTE_VALIDADO', %s, %s, %s)
            """,
            (cancelacion_id, linea_id, pedido_id, cantidad, resolucion_id, usuario_id, reembolso_id),
        )

        _registrar_movimiento_cancelacion(
            cur,
            darkstore_id,
            producto_id,
            cantidad,
            "Cancelación por faltante validado",
            clave_operacion,
            reserva_id,
            linea_id,
            usuario_id=usuario_id,
            resolucion_id=resolucion_id,
        )

        cur.execute(
            "SELECT bool_and(estado = 'CANCELADA') FROM linea_pedido WHERE pedido_id = %s",
            (pedido_id,),
        )
        todas_canceladas = cur.fetchone()[0]
        cur.execute(
            """
            UPDATE pedido
            SET total_vigente = total_vigente - %s,
                estado = CASE WHEN %s THEN 'CANCELADO' ELSE estado END
            WHERE pedido_id = %s
            """,
            (monto, todas_canceladas, pedido_id),
        )

        registrar_aviso(
            conn,
            tipo="CANCELACION_PARCIAL",
            origen_tipo="CANCELACION_LINEA",
            origen_id=cancelacion_id,
            cliente_id=cliente_id,
            pedido_id=pedido_id,
        )

    return reembolso_a_enviar


@router.post("/v1/pedidos/{pedido_id}/cancelacion")
def cancelar_pedido_http(
    pedido_id: UUID,
    cuerpo: CancelarPedidoSolicitud,
    response: Response,
    clave_operacion: str | None = Header(default=None, alias="Clave-Operacion"),
    cliente_id: str | None = Header(default=None, alias="Cliente-Id"),
):
    from app.modulos.pagos import enviar_reembolso

    clave = validar_clave_operacion(clave_operacion)
    cliente = _parsear_cliente(cliente_id)
    huella = calcular_huella("CL-13", pedido_id, {"cliente_id": str(cliente), "motivo": cuerpo.motivo})
    por_enviar: list[UUID] = []

    def trabajo() -> dict:
        resultado, reembolso = cancelar_pedido_cliente(conn, pedido_id, cliente, cuerpo.motivo, clave)
        if reembolso is not None:
            por_enviar.append(reembolso)
        return resultado

    with pool.connection() as conn:
        respuesta, previa = ejecutar_idempotente(
            conn,
            clave=clave,
            operacion="CL-13",
            objeto="pedido",
            objeto_id=pedido_id,
            actor_tipo="CLIENTE",
            huella=huella,
            trabajo=trabajo,
        )

    for reembolso_id in por_enviar:
        enviar_reembolso(reembolso_id)

    response.status_code = respuesta["estado_http"]
    if previa:
        response.headers["Resultado-Previo"] = "true"
    return respuesta["cuerpo"]
