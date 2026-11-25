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


def _pedido_del_cliente(cur, pedido_id: UUID, cliente_id: UUID) -> tuple:
    cur.execute("SELECT reserva_id, cliente_id, darkstore_id, estado, total_vigente, intento_aplicado_id FROM pedido "
                "WHERE pedido_id = %s FOR UPDATE", (pedido_id,))
    fila = cur.fetchone()
    if fila is None or fila[1] != cliente_id:
        raise ErrorDeNegocio(404, "NO_ENCONTRADO", "El pedido no existe o no pertenece al cliente.")
    if fila[3] != "CONFIRMADO":
        raise ErrorDeNegocio(409, "ESTADO_INCOMPATIBLE", f"El pedido está en estado {fila[3]} y no admite cancelación.")
    return fila[0], fila[2], fila[4], fila[5]


def _lineas_sin_preparar(cur, pedido_id: UUID, darkstore_id: UUID) -> list[tuple]:
    cur.execute("SELECT producto_id FROM linea_pedido WHERE pedido_id = %s ORDER BY producto_id", (pedido_id,))
    _bloquear_inventario(cur, darkstore_id, [f[0] for f in cur.fetchall()])
    cur.execute("SELECT linea_id, producto_id, q, s, k, estado FROM linea_pedido WHERE pedido_id = %s ORDER BY linea_id FOR UPDATE",
                (pedido_id,))
    lineas = cur.fetchall()
    if any(s > 0 for _, _, _, s, _, _ in lineas):
        raise ErrorDeNegocio(409, "PREPARACION_INICIADA", "La preparación del pedido ya inició; el cliente ya no puede cancelarlo.")
    if any(estado not in ("ASIGNADA", "CANCELADA") for *_, estado in lineas):
        raise ErrorDeNegocio(409, "ESTADO_INCOMPATIBLE", "El pedido tiene una línea con un faltante en validación; se resuelve con D-05.")
    return [l for l in lineas if l[2] - l[4] > 0]


def _respuesta_cancelacion(pedido_id: UUID, total, reembolso_id: UUID | None) -> dict:
    return {
        "estado_http": 200,
        "cuerpo": {"pedido_id": str(pedido_id), "estado": "CANCELADO", "total_vigente": "0.00",
                   "ajuste_pendiente": reembolso_id is not None,
                   "reembolso_id": str(reembolso_id) if reembolso_id else None, "monto_reembolso": f"{total:.2f}"},
        "estado_anterior": {"pedido": "CONFIRMADO", "total_vigente": f"{total:.2f}"},
        "estado_resultante": {"pedido": "CANCELADO", "total_vigente": "0.00"},
    }


def cancelar_pedido_cliente(conn, pedido_id: UUID, cliente_id: UUID, motivo: str,
                            clave_operacion: UUID) -> tuple[dict, UUID | None]:
    from app.modulos.pagos import solicitar_reembolso

    with conn.cursor() as cur:
        reserva_id, darkstore_id, total, intento_id = _pedido_del_cliente(cur, pedido_id, cliente_id)
        for linea_id, producto_id, q, _, k, _ in _lineas_sin_preparar(cur, pedido_id, darkstore_id):
            cur.execute("UPDATE linea_pedido SET k = q, estado = 'CANCELADA' WHERE linea_id = %s", (linea_id,))
            _registrar_movimiento_cancelacion(cur, darkstore_id, producto_id, q - k, f"Cancelación del cliente: {motivo}",
                                              clave_operacion, reserva_id, linea_id)
        cur.execute("UPDATE pedido SET estado = 'CANCELADO', total_vigente = 0 WHERE pedido_id = %s", (pedido_id,))
        por_enviar = solicitar_reembolso(conn, intento_id, total, "CANCELACION_CLIENTE") if total > 0 else None
        reembolso_id = _reembolso_total_de(cur, intento_id) if total > 0 else None
        registrar_aviso(conn, tipo="PEDIDO_CANCELADO", origen_tipo="PEDIDO", origen_id=pedido_id,
                        cliente_id=cliente_id, pedido_id=pedido_id)
    return _respuesta_cancelacion(pedido_id, total, reembolso_id), por_enviar


def estado_tras_cancelar(q: int, s: int, k: int, estado: str) -> str:
    if s + k != q:
        return estado
    return "CANCELADA" if s == 0 else "PARCIAL"


def _asignacion_pendiente(cur, resolucion_id: UUID, linea_id: UUID) -> tuple | None:
    cur.execute("SELECT a.canceladas, lp.pedido_id FROM asignacion_faltante a JOIN linea_pedido lp USING (linea_id) "
                "WHERE a.resolucion_id = %s AND a.linea_id = %s", (resolucion_id, linea_id))
    fila = cur.fetchone()
    if fila is None:
        raise ErrorDeNegocio(404, "NO_ENCONTRADO", "No existe la asignación del faltante para esa línea.")
    if fila[0] == 0:
        return None
    cur.execute("SELECT reserva_id, cliente_id, darkstore_id, estado, intento_aplicado_id FROM pedido WHERE pedido_id = %s FOR UPDATE",
                (fila[1],))
    pedido = cur.fetchone()
    cur.execute("SELECT 1 FROM cancelacion_linea WHERE resolucion_id = %s AND linea_id = %s", (resolucion_id, linea_id))
    if cur.fetchone() is not None:
        return None
    if pedido[3] != "CONFIRMADO":
        raise ErrorDeNegocio(409, "ESTADO_INCOMPATIBLE", f"El pedido está en estado {pedido[3]} y no admite cancelación por faltante.")
    return fila[0], fila[1], pedido


def _restar_de_la_linea(cur, linea_id: UUID, darkstore_id: UUID, cantidad: int) -> tuple[UUID, Decimal]:
    cur.execute("SELECT producto_id FROM linea_pedido WHERE linea_id = %s", (linea_id,))
    producto_id = cur.fetchone()[0]
    _bloquear_inventario(cur, darkstore_id, [producto_id])
    cur.execute("SELECT q, s, k, precio_unitario, estado FROM linea_pedido WHERE linea_id = %s FOR UPDATE", (linea_id,))
    q, s, k, precio, estado = cur.fetchone()
    if cantidad > q - s - k:
        raise ErrorDeNegocio(409, "ESTADO_INCOMPATIBLE", "La línea no tiene suficientes unidades pendientes para cancelar.")
    cur.execute("UPDATE linea_pedido SET k = %s, estado = %s WHERE linea_id = %s",
                (k + cantidad, estado_tras_cancelar(q, s, k + cantidad, estado), linea_id))
    return producto_id, (Decimal(precio) * cantidad).quantize(Decimal("0.01"))


def cancelar_cantidad_por_faltante(conn, resolucion_id: UUID, linea_id: UUID, usuario_id: UUID,
                                   clave_operacion: UUID) -> UUID | None:
    from app.modulos.pagos import solicitar_reembolso

    with conn.cursor() as cur:
        pendiente = _asignacion_pendiente(cur, resolucion_id, linea_id)
        if pendiente is None:
            return None
        cantidad, pedido_id, (reserva_id, cliente_id, darkstore_id, _, intento_id) = pendiente
        producto_id, monto = _restar_de_la_linea(cur, linea_id, darkstore_id, cantidad)
        cancelacion_id = uuid4()
        por_enviar = solicitar_reembolso(conn, intento_id, monto, "CANCELACION_POR_FALTANTE", cancelacion_id=cancelacion_id)
        cur.execute(
            "INSERT INTO cancelacion_linea (cancelacion_id, linea_id, pedido_id, cantidad, causa, resolucion_id, usuario_id, reembolso_id) "
            "SELECT %s, %s, %s, %s, 'FALTANTE_VALIDADO', %s, %s, reembolso_id FROM reembolso WHERE cancelacion_id = %s",
            (cancelacion_id, linea_id, pedido_id, cantidad, resolucion_id, usuario_id, cancelacion_id),
        )
        _registrar_movimiento_cancelacion(cur, darkstore_id, producto_id, cantidad, "Cancelación por faltante validado",
                                          clave_operacion, reserva_id, linea_id, usuario_id=usuario_id, resolucion_id=resolucion_id)
        cur.execute(
            "UPDATE pedido SET total_vigente = total_vigente - %s, estado = CASE WHEN "
            "(SELECT bool_and(estado = 'CANCELADA') FROM linea_pedido WHERE pedido_id = %s) THEN 'CANCELADO' ELSE estado END "
            "WHERE pedido_id = %s",
            (monto, pedido_id, pedido_id),
        )
        registrar_aviso(conn, tipo="CANCELACION_PARCIAL", origen_tipo="CANCELACION_LINEA", origen_id=cancelacion_id,
                        cliente_id=cliente_id, pedido_id=pedido_id)
    return por_enviar


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
