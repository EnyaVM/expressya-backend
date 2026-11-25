import logging
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from app.db import pool
from app.errores import ErrorDeNegocio
from app.idempotencia import (
    calcular_huella,
    ejecutar_idempotente,
    validar_clave_operacion,
)
from app.modulos.avisos import registrar_aviso
from app.modulos.reservas import cerrar_si_sigue_activa


class RutaPedidoValidada(APIRoute):
    def get_route_handler(self):
        atender = super().get_route_handler()

        async def atender_validado(peticion: Request):
            try:
                return await atender(peticion)
            except RequestValidationError as error:
                campos = [
                    ".".join(map(str, fallo["loc"]))
                    for fallo in error.errors()
                ]
                raise ErrorDeNegocio(
                    400,
                    "SOLICITUD_INVALIDA",
                    "Revisa los campos de la solicitud.",
                    {"campos": campos},
                ) from error

        return atender_validado


router = APIRouter(
    tags=["Pedidos (M03)"],
    route_class=RutaPedidoValidada,
)



class SepararCantidadSolicitud(BaseModel):
    cantidad: int = Field(gt=0)

MAXIMO_INTENTOS_EVENTO = 5
registro_eventos = logging.getLogger("expressya.eventos")


def _disparar_reembolso_interno(
    conn,
    intento_id: UUID,
    monto: Decimal,
    motivo: str,
) -> UUID | None:
    """Importación diferida para evitar ciclos entre M03 y M04 al iniciar."""
    from app.modulos.pagos import solicitar_reembolso

    return solicitar_reembolso(conn, intento_id=intento_id, monto=monto, motivo=motivo)


def reembolsar_productos_retirados(conn, intento_id: UUID, reserva_id: UUID) -> UUID | None:
    from app.modulos.pagos import solicitar_reembolso

    retiradas = conn.execute(
        """
        SELECT lr.producto_id, lr.cantidad * lr.precio_unitario
        FROM linea_reserva lr
        JOIN intento_pago i ON i.intento_id = %s
        WHERE lr.reserva_id = %s
          AND lr.fecha_retiro > i.fecha_inicio
        ORDER BY lr.producto_id
        """,
        (intento_id, reserva_id),
    ).fetchall()
    primeros = [
        solicitar_reembolso(conn, intento_id=intento_id, monto=monto, motivo="PRODUCTO_RETIRADO",
                            reserva_origen_id=reserva_id, producto_origen_id=producto_id)
        for producto_id, monto in retiradas
    ]
    return next((r for r in primeros if r is not None), None)


ORDEN_DE_BLOQUEO_CL07 = ("pedido", "reserva", "linea_reserva", "inventario", "intento_pago", "reembolso")


def _bloquear_para_confirmar(conn, intento_id: UUID) -> dict | None:
    fila = conn.execute("SELECT pedido_id, monto FROM intento_pago WHERE intento_id = %s", (intento_id,)).fetchone()
    if fila is None:
        return None
    pedido_id, monto = fila
    reserva_id, darkstore_id, estado_pedido, aplicado = conn.execute(
        "SELECT reserva_id, darkstore_id, estado, intento_aplicado_id FROM pedido WHERE pedido_id = %s FOR UPDATE", (pedido_id,)
    ).fetchone()
    estado_reserva, motivo_cierre = conn.execute(
        "SELECT estado, motivo_cierre FROM reserva WHERE reserva_id = %s FOR UPDATE", (reserva_id,)).fetchone()
    lineas = conn.execute(
        "SELECT producto_id, cantidad, precio_unitario FROM linea_reserva WHERE reserva_id = %s AND fecha_retiro IS NULL "
        "ORDER BY producto_id FOR UPDATE", (reserva_id,)).fetchall()
    conn.execute("SELECT 1 FROM inventario WHERE darkstore_id = %s AND producto_id = ANY(%s) ORDER BY producto_id FOR UPDATE",
                 (darkstore_id, [l[0] for l in lineas]))
    estado_intento = conn.execute("SELECT estado FROM intento_pago WHERE intento_id = %s FOR UPDATE", (intento_id,)).fetchone()[0]
    return {"pedido_id": pedido_id, "monto": monto, "reserva_id": reserva_id, "darkstore_id": darkstore_id,
            "estado_pedido": estado_pedido, "ya_aplicado": aplicado == intento_id, "estado_reserva": estado_reserva,
            "motivo_cierre": motivo_cierre, "lineas": lineas, "estado_intento": estado_intento}


def _comprometer_linea(conn, d: dict, producto_id: UUID, cantidad: int, precio) -> None:
    conn.execute(
        "INSERT INTO linea_pedido (linea_id, pedido_id, reserva_id, producto_id, darkstore_id, precio_unitario, q, s, k, "
        "fecha_compromiso, estado) VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, %s, 0, 0, clock_timestamp(), 'ASIGNADA')",
        (d["pedido_id"], d["reserva_id"], producto_id, d["darkstore_id"], precio, cantidad),
    )
    movimiento_id = conn.execute(
        "INSERT INTO movimiento_inventario (darkstore_id, producto_id, tipo, fecha, tx_origen, motivo, clave_operacion, "
        "referencia_reserva_id) VALUES (%s, %s, 'COMPROMISO', clock_timestamp(), NULL, "
        "'Compromiso de inventario por pago aprobado', %s, %s) RETURNING movimiento_id",
        (d["darkstore_id"], producto_id, uuid4(), d["reserva_id"]),
    ).fetchone()[0]
    conn.execute("INSERT INTO movimiento_saldo (movimiento_id, saldo, cantidad) VALUES (%s, 'R', %s), (%s, 'C', %s)",
                 (movimiento_id, -cantidad, movimiento_id, cantidad))


def _confirmar_a_tiempo(conn, intento_id: UUID, d: dict) -> bool:
    comprometida = conn.execute(
        "UPDATE reserva SET estado = 'COMPROMETIDA', motivo_cierre = 'PAGO_CONFIRMADO', fecha_cierre = clock_timestamp() "
        "WHERE reserva_id = %s AND estado = 'ACTIVA' AND clock_timestamp() < t_vence RETURNING reserva_id",
        (d["reserva_id"],),
    ).fetchone()
    if comprometida is None:
        return False
    for producto_id, cantidad, precio in d["lineas"]:
        _comprometer_linea(conn, d, producto_id, cantidad, precio)
    conn.execute("UPDATE pedido SET estado = 'CONFIRMADO', intento_aplicado_id = %s WHERE pedido_id = %s",
                 (intento_id, d["pedido_id"]))
    return True


def _reembolsar_pago_tardio(conn, intento_id: UUID, d: dict) -> UUID | None:
    if d["estado_reserva"] == "ACTIVA":
        cerrar_si_sigue_activa(conn, d["reserva_id"], "EXPIRADA", "VENCIMIENTO", "EXPIRACION",
                               "Vencimiento de la reserva (300 s)")
    conn.execute("UPDATE pedido SET estado = 'SIN_COMPRA', ajuste_pendiente = true WHERE pedido_id = %s", (d["pedido_id"],))
    motivo = "PRODUCTO_RETIRADO" if d["motivo_cierre"] == "FALTANTE" else "PAGO_TARDIO"
    return _disparar_reembolso_interno(conn, intento_id=intento_id, monto=d["monto"], motivo=motivo)


def confirmar_compra(conn, intento_id: UUID) -> UUID | None:
    d = _bloquear_para_confirmar(conn, intento_id)
    if d is None or d["ya_aplicado"] or d["estado_intento"] != "APROBADO":
        return None
    if d["estado_pedido"] in ("CONFIRMADO", "CANCELADO"):
        return _disparar_reembolso_interno(conn, intento_id=intento_id, monto=d["monto"], motivo="COBRO_DUPLICADO")
    if _confirmar_a_tiempo(conn, intento_id, d):
        return reembolsar_productos_retirados(conn, intento_id, d["reserva_id"])
    return _reembolsar_pago_tardio(conn, intento_id, d)


def avisar_eventos_detenidos() -> int:
    with pool.connection() as conn:
        detenidos = conn.execute(
            """
            SELECT evento_id
            FROM evento_interno
            WHERE NOT procesado AND tipo = 'EV-01' AND intentos_procesamiento >= %s
            """,
            (MAXIMO_INTENTOS_EVENTO,),
        ).fetchall()
        for (evento_id,) in detenidos:
            registrar_aviso(conn, "EVENTO_SIN_PROCESAR", "EVENTO_INTERNO", evento_id)
    return len(detenidos)


def procesar_eventos_pendientes(limite: int = 50) -> int:
    """
    Consumidor de eventos EV-01. Procesa hasta `limite` eventos en transacciones individuales
    con FOR UPDATE SKIP LOCKED. Envía los reembolsos pendientes fuera de la transacción.
    """
    from app.modulos.pagos import enviar_reembolso

    avisar_eventos_detenidos()

    procesados = 0
    intentados: list[UUID] = []
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
                          AND intentos_procesamiento < %s
                          AND NOT (evento_id = ANY(%s::uuid[]))
                        ORDER BY intentos_procesamiento ASC, fecha ASC
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                        """,
                        (MAXIMO_INTENTOS_EVENTO, intentados),
                    )
                    fila = cur.fetchone()
                    if fila is None:
                        break
                    evento_id, intento_id = fila
                    intentados.append(evento_id)

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
                registro_eventos.exception("Fallo el evento %s", evento_id)
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE evento_interno SET intentos_procesamiento = intentos_procesamiento + 1 WHERE evento_id = %s",
                            (evento_id,),
                        )
                continue

        if reembolso_a_enviar is not None:
            enviar_reembolso(reembolso_a_enviar)

    return procesados


def estado_tras_separar(q: int, s: int, k: int) -> str:
    if s == q:
        return "SEPARADA"
    return "PARCIAL" if s + k == q else "ASIGNADA"


def _linea_a_separar(conn, linea_id: UUID) -> tuple:
    fila = conn.execute("SELECT pedido_id, darkstore_id, producto_id, reserva_id FROM linea_pedido WHERE linea_id = %s",
                        (linea_id,)).fetchone()
    if fila is None:
        raise ErrorDeNegocio(404, "NO_ENCONTRADO", "Línea inexistente.")
    return fila


def _exigir_picker(conn, usuario_id: UUID, tienda_id: UUID) -> None:
    if conn.execute("SELECT 1 FROM asignacion_rol WHERE usuario_id = %s AND darkstore_id = %s AND rol = 'PICKER'",
                    (usuario_id, tienda_id)).fetchone() is None:
        raise ErrorDeNegocio(403, "SIN_PERMISO", "Usuario sin rol PICKER en la tienda.")


def _exigir_existencias(conn, tienda_id: UUID, producto_id: UUID, cantidad: int) -> None:
    e, estado = conn.execute("SELECT e, estado_producto FROM inventario WHERE darkstore_id = %s AND producto_id = %s FOR UPDATE",
                             (tienda_id, producto_id)).fetchone()
    if estado != "HABILITADO":
        raise ErrorDeNegocio(409, "PRODUCTO_BLOQUEADO", "Producto no habilitado.")
    if e < cantidad:
        raise ErrorDeNegocio(409, "SIN_DISPONIBLE", "Cantidad no disponible.")


def separar_cantidad(conn, linea_id: UUID, cantidad: int, usuario_id: UUID, clave_operacion: UUID) -> dict:
    pedido_id, tienda_id, producto_id, reserva_id = _linea_a_separar(conn, linea_id)
    if conn.execute("SELECT estado FROM pedido WHERE pedido_id = %s FOR UPDATE", (pedido_id,)).fetchone()[0] != "CONFIRMADO":
        raise ErrorDeNegocio(409, "ESTADO_INCOMPATIBLE", "Pedido no confirmado.")
    _exigir_picker(conn, usuario_id, tienda_id)
    _exigir_existencias(conn, tienda_id, producto_id, cantidad)
    q, s, k, estado = conn.execute("SELECT q, s, k, estado FROM linea_pedido WHERE linea_id = %s FOR UPDATE", (linea_id,)).fetchone()
    if estado != "ASIGNADA" or s + k + cantidad > q:
        raise ErrorDeNegocio(409, "ESTADO_INCOMPATIBLE", "La línea no admite esa cantidad.")
    nuevo_estado = estado_tras_separar(q, s + cantidad, k)
    conn.execute("UPDATE linea_pedido SET s = %s, estado = %s WHERE linea_id = %s", (s + cantidad, nuevo_estado, linea_id))
    conn.execute("UPDATE inventario SET e = e - %s WHERE darkstore_id = %s AND producto_id = %s", (cantidad, tienda_id, producto_id))
    movimiento_id = conn.execute(
        "INSERT INTO movimiento_inventario (darkstore_id, producto_id, tipo, motivo, usuario_id, clave_operacion, "
        "referencia_reserva_id, referencia_linea_id) VALUES (%s, %s, 'SEPARACION', 'Separación CL-08', %s, %s, %s, %s) "
        "RETURNING movimiento_id",
        (tienda_id, producto_id, usuario_id, clave_operacion, reserva_id, linea_id),
    ).fetchone()[0]
    conn.execute("INSERT INTO movimiento_saldo (movimiento_id, saldo, cantidad) VALUES (%s, 'E', %s), (%s, 'C', %s)",
                 (movimiento_id, -cantidad, movimiento_id, -cantidad))
    return {"linea_id": str(linea_id), "cantidad_separada": s + cantidad, "estado": nuevo_estado}


@router.post("/v1/lineas-pedido/{linea_id}/separaciones")
@router.post("/v1/lineas-pedido/{linea_id}/separacion", include_in_schema=False)
def separar_cantidad_http(
    linea_id: UUID,
    cuerpo: SepararCantidadSolicitud,
    response: Response,
    clave_operacion: str | None = Header(
        default=None, alias="Clave-Operacion"
    ),
    usuario_id: str | None = Header(
        default=None, alias="Usuario-Id"
    ),
):
    # Validar clave de operación.
    clave = validar_clave_operacion(clave_operacion)

    # Validar usuario operativo.
    if not usuario_id:
        raise ErrorDeNegocio(
            401, "NO_AUTENTICADO", "Falta Usuario-Id."
        )

    try:
        usuario = UUID(usuario_id.strip())
    except ValueError as exc:
        raise ErrorDeNegocio(
            400, "SOLICITUD_INVALIDA", "Usuario-Id inválido."
        ) from exc

    # Comprobar que el usuario exista en la base de datos.
    with pool.connection() as conn:
        existe = conn.execute(
            """
            SELECT 1 FROM usuario_operativo
            WHERE usuario_id = %s
            """,
            (usuario,),
        ).fetchone()

    if existe is None:
        raise ErrorDeNegocio(
            403,
            "SIN_PERMISO",
            "El usuario operativo no está registrado.",
        )

    # Identificar solicitudes repetidas.
    huella = calcular_huella(
        "CL-08",
        linea_id,
        {
            "cantidad": cuerpo.cantidad,
            "usuario_id": str(usuario),
        },
    )

    with pool.connection() as conn:

        def trabajo():
            resultado = separar_cantidad(
                conn, linea_id, cuerpo.cantidad, usuario, clave
            )
            return {
                "estado_http": 200,
                "cuerpo": resultado,
            }

        resultado, previa = ejecutar_idempotente(
            conn,
            clave=clave,
            operacion="CL-08",
            objeto="LINEA_PEDIDO",
            objeto_id=linea_id,
            actor_tipo="USUARIO_OPERATIVO",
            usuario_id=usuario,
            huella=huella,
            trabajo=trabajo,
        )

    response.status_code = resultado["estado_http"]

    if previa:
        response.headers["Resultado-Previo"] = "true"

    return resultado["cuerpo"]
