from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal, InvalidOperation
import time
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from fastapi import APIRouter, Header, Response
from psycopg.errors import DeadlockDetected, SerializationFailure, UniqueViolation
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

import psycopg

from app.db import DATABASE_URL, pool
from app.errores import ErrorDeNegocio
from app.idempotencia import calcular_huella, ejecutar_idempotente, validar_clave_operacion
from app.modulos import pasarela_simulada
from app.modulos.pedidos import procesar_eventos_pendientes
from app.modulos.reservas import exigir_reserva_vigente

router = APIRouter(tags=["Pagos y Reembolsos (M04)"])

PLAZO_PASARELA_SEGUNDOS = 30


def _formatear_dinero(valor: Decimal) -> str:
    return f"{valor:.2f}"


MONTO_MAXIMO = Decimal("999999999999.99")


def _parsear_dinero(texto: str) -> Decimal:
    try:
        valor = Decimal(str(texto).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ErrorDeNegocio(400, "SOLICITUD_INVALIDA", "Formato de monto inválido.") from exc
    if not valor.is_finite():
        raise ErrorDeNegocio(400, "SOLICITUD_INVALIDA", "El monto debe ser un número finito.")
    if valor <= 0 or valor > MONTO_MAXIMO or valor.as_tuple().exponent < -2:
        raise ErrorDeNegocio(
            400,
            "SOLICITUD_INVALIDA",
            "El monto debe ser mayor a cero, con dos decimales y dentro del límite permitido.",
        )
    return valor.quantize(Decimal("0.01"))


def _parsear_uuid_cabecera(valor: str | None, nombre_cabecera: str) -> UUID:
    if not valor:
        raise ErrorDeNegocio(
            401,
            "NO_AUTENTICADO",
            f"Falta la cabecera obligatoria {nombre_cabecera}.",
        )
    try:
        return UUID(str(valor).strip())
    except ValueError as exc:
        raise ErrorDeNegocio(
            400,
            "SOLICITUD_INVALIDA",
            f"La cabecera {nombre_cabecera} debe ser un UUID válido.",
        ) from exc


def _exigir_rol_responsable(conn, usuario_id: UUID) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM asignacion_rol
            WHERE usuario_id = %s AND rol = 'RESPONSABLE'
            LIMIT 1
            """,
            (usuario_id,),
        )
        if cur.fetchone() is None:
            raise ErrorDeNegocio(
                403,
                "SIN_PERMISO",
                "El usuario no cuenta con el rol RESPONSABLE requerido.",
            )


def _ejecutar_con_reintento_concurrencia(fn):
    """Reintenta hasta 3 veces ante 40P01 (deadlock) o 40001 (serialization failure)."""
    intentos = 0
    while True:
        try:
            return fn()
        except (DeadlockDetected, SerializationFailure):
            intentos += 1
            if intentos >= 3:
                raise
            time.sleep(0.02 * intentos)


def _serializar_intento(
    intento_id: UUID,
    pedido_id: UUID,
    monto: Decimal,
    estado: str,
    t_vence_reserva: datetime,
) -> dict[str, Any]:
    return {
        "intento_id": str(intento_id),
        "pedido_id": str(pedido_id),
        "monto": _formatear_dinero(monto),
        "estado": estado,
        "t_vence_reserva": t_vence_reserva.isoformat(),
    }


def _serializar_reembolso(
    reembolso_id: UUID,
    intento_id: UUID,
    pedido_id: UUID,
    monto: Decimal,
    motivo: str,
    estado_intento: str,
    referencia_externa: str | None,
) -> dict[str, Any]:
    return {
        "reembolso_id": str(reembolso_id),
        "intento_id": str(intento_id),
        "pedido_id": str(pedido_id),
        "monto": _formatear_dinero(monto),
        "motivo": motivo,
        "estado": estado_intento,
        "referencia_externa": referencia_externa,
    }


def aplicar_resultado_intento(conn, intento_id: UUID, resultado: str, referencia_externa: str | None = None) -> str:
    fila = conn.execute("SELECT estado FROM intento_pago WHERE intento_id = %s FOR UPDATE", (intento_id,)).fetchone()
    if fila is None:
        raise ErrorDeNegocio(404, "NO_ENCONTRADO", "El intento de pago no existe.")
    if fila[0] not in ("INICIADO", "EN_VERIFICACION"):
        return fila[0]
    conn.execute(
        "UPDATE intento_pago SET estado = %s, referencia_externa = COALESCE(%s, referencia_externa), "
        "fecha_resultado = clock_timestamp() WHERE intento_id = %s",
        (resultado, referencia_externa, intento_id),
    )
    if resultado == "APROBADO":
        conn.execute("INSERT INTO evento_interno (evento_id, tipo, productor, intento_id, fecha, procesado) "
                     "VALUES (gen_random_uuid(), 'EV-01', 'M04', %s, clock_timestamp(), false)", (intento_id,))
    return resultado


def _siguiente_en_cola(conn, intento_id: UUID, estado: str) -> UUID | None:
    if estado != "REEMBOLSO_EN_PROCESO":
        return None
    return conn.execute("SELECT reembolso_actual_id FROM intento_pago WHERE intento_id = %s", (intento_id,)).fetchone()[0]


def _aplicar_resultado_reembolso(
    conn,
    reembolso_id: UUID,
    resultado: str,
    referencia_externa: str | None = None,
) -> str:
    """
    Aplica CONFIRMADO o FALLIDO a un reembolso y actualiza el intento asociado.
    Devuelve el nuevo estado del intento_pago.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT r.reembolso_id, r.intento_id, r.pedido_id, r.monto,
                   r.fecha_confirmacion, r.fecha_fallo_confirmado,
                   i.monto, i.estado, i.reembolso_actual_id
            FROM reembolso r
            JOIN intento_pago i ON i.intento_id = r.intento_id
            WHERE r.reembolso_id = %s
            FOR UPDATE OF i, r
            """,
            (reembolso_id,),
        )
        fila = cur.fetchone()
        if fila is None:
            raise ErrorDeNegocio(404, "NO_ENCONTRADO", "El reembolso no existe.")

        (
            _,
            intento_id,
            _,
            _,
            fecha_confirmacion,
            fecha_fallo_confirmado,
            monto_cobrado,
            estado_intento,
            reembolso_actual_id,
        ) = fila

        # Si este reembolso ya cerró definitivamente, no lo alteramos
        if fecha_confirmacion is not None:
            return estado_intento
        if resultado == "FALLIDO" and fecha_fallo_confirmado is not None:
            return estado_intento

        if resultado == "CONFIRMADO":
            cur.execute(
                """
                UPDATE reembolso
                SET fecha_confirmacion = clock_timestamp(),
                    fecha_fallo_confirmado = NULL,
                    referencia_externa = COALESCE(%s, referencia_externa)
                WHERE reembolso_id = %s
                """,
                (referencia_externa, reembolso_id),
            )
            cur.execute(
                """
                SELECT COALESCE(SUM(monto), 0)
                FROM reembolso
                WHERE intento_id = %s AND fecha_confirmacion IS NOT NULL
                """,
                (intento_id,),
            )
            total_reembolsado = cur.fetchone()[0]
            nuevo_estado_intento = (
                "REEMBOLSADO_TOTAL" if total_reembolsado >= monto_cobrado else "REEMBOLSADO_PARCIAL"
            )

            # Verificar si hay otro reembolso en cola esperando envío
            cur.execute(
                """
                SELECT reembolso_id
                FROM reembolso
                WHERE intento_id = %s
                  AND reembolso_id <> %s
                  AND fecha_envio IS NULL
                  AND fecha_confirmacion IS NULL
                ORDER BY fecha_solicitud ASC
                LIMIT 1
                """,
                (intento_id, reembolso_id),
            )
            siguiente = cur.fetchone()
            if siguiente is not None:
                cur.execute(
                    """
                    UPDATE intento_pago
                    SET estado = 'REEMBOLSO_EN_PROCESO',
                        reembolso_actual_id = %s
                    WHERE intento_id = %s
                    """,
                    (siguiente[0], intento_id),
                )
                return "REEMBOLSO_EN_PROCESO"

            cur.execute(
                """
                UPDATE intento_pago
                SET estado = %s,
                    reembolso_actual_id = NULL
                WHERE intento_id = %s
                """,
                (nuevo_estado_intento, intento_id),
            )
            return nuevo_estado_intento

        # Caso FALLIDO
        cur.execute(
            """
            UPDATE reembolso
            SET fecha_fallo_confirmado = clock_timestamp(),
                referencia_externa = COALESCE(%s, referencia_externa)
            WHERE reembolso_id = %s
            """,
            (referencia_externa, reembolso_id),
        )
        cur.execute(
            """
            UPDATE intento_pago
            SET estado = 'REEMBOLSO_FALLIDO',
                reembolso_actual_id = %s
            WHERE intento_id = %s
            """,
            (reembolso_id, intento_id),
        )
        return "REEMBOLSO_FALLIDO"


def solicitar_reembolso(
    conn,
    intento_id: UUID,
    monto: Decimal,
    motivo: str,
    cancelacion_id: UUID | None = None,
    reserva_origen_id: UUID | None = None,
    producto_origen_id: UUID | None = None,
) -> UUID | None:
    """
    CL-16 (interno M04): Registra la solicitud de reembolso en la transacción actual.
    Devuelve reembolso_id si queda activo para envío inmediato, o None si queda en espera
    porque ya existe otro reembolso en curso para ese intento.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT intento_id, pedido_id, monto, estado, reembolso_actual_id
            FROM intento_pago
            WHERE intento_id = %s
            FOR UPDATE
            """,
            (intento_id,),
        )
        fila = cur.fetchone()
        if fila is None:
            raise ErrorDeNegocio(404, "NO_ENCONTRADO", "El intento de pago no existe.")

        _, pedido_id, _, estado_intento, reembolso_actual_id = fila
        reembolso_id = uuid4()

        cur.execute(
            """
            INSERT INTO reembolso (
                reembolso_id, intento_id, pedido_id, monto, motivo,
                cancelacion_id, reserva_origen_id, producto_origen_id,
                fecha_solicitud, fecha_envio
            )
            VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s,
                clock_timestamp(), NULL
            )
            """,
            (
                reembolso_id,
                intento_id,
                pedido_id,
                monto,
                motivo,
                cancelacion_id,
                reserva_origen_id,
                producto_origen_id,
            ),
        )

        cur.execute(
            """
            UPDATE pedido
            SET ajuste_pendiente = true
            WHERE pedido_id = %s
            """,
            (pedido_id,),
        )

        # Un reembolso a la vez por cobro: si ya hay uno en curso, este queda en espera
        if reembolso_actual_id is not None:
            return None

        cur.execute(
            """
            UPDATE intento_pago
            SET estado = 'REEMBOLSO_EN_PROCESO',
                reembolso_actual_id = %s
            WHERE intento_id = %s
            """,
            (reembolso_id, intento_id),
        )
        return reembolso_id


def _marcar_envio(reembolso_id: UUID) -> str | None:
    with pool.connection() as conn, conn.transaction():
        fila = conn.execute(
            "SELECT r.monto, r.fecha_envio, i.estado, i.reembolso_actual_id FROM reembolso r "
            "JOIN intento_pago i ON i.intento_id = r.intento_id WHERE r.reembolso_id = %s FOR UPDATE OF i, r",
            (reembolso_id,),
        ).fetchone()
        if fila is None or fila[1] is not None or fila[3] != reembolso_id or fila[2] != "REEMBOLSO_EN_PROCESO":
            return None
        conn.execute("UPDATE reembolso SET fecha_envio = clock_timestamp() WHERE reembolso_id = %s", (reembolso_id,))
        return _formatear_dinero(fila[0])


def _registrar_respuesta_reembolso(reembolso_id: UUID, respuesta) -> UUID | None:
    with pool.connection() as conn, conn.transaction():
        if respuesta is None:
            conn.execute("UPDATE intento_pago SET estado = 'REEMBOLSO_EN_VERIFICACION' "
                         "WHERE reembolso_actual_id = %s AND estado = 'REEMBOLSO_EN_PROCESO'", (reembolso_id,))
            return None
        resultado, referencia = respuesta
        estado = _aplicar_resultado_reembolso(conn, reembolso_id=reembolso_id, resultado=resultado, referencia_externa=referencia)
        intento_id = conn.execute("SELECT intento_id FROM reembolso WHERE reembolso_id = %s", (reembolso_id,)).fetchone()[0]
        return _siguiente_en_cola(conn, intento_id, estado)


def enviar_reembolso(reembolso_id: UUID) -> None:
    monto = _marcar_envio(reembolso_id)
    if monto is None:
        return
    siguiente = _registrar_respuesta_reembolso(reembolso_id, pasarela_simulada.reembolsar(reembolso_id, monto))
    if siguiente is not None:
        enviar_reembolso(siguiente)


def recuperar_intentos_iniciados(segundos: int = PLAZO_PASARELA_SEGUNDOS) -> int:
    """
    Recuperación tras caída: cualquier intento INICIADO que supere el plazo sin respuesta
    pasa a EN_VERIFICACION. Nunca se vuelve a cobrar.
    """
    with pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE intento_pago
                    SET estado = 'EN_VERIFICACION'
                    WHERE estado = 'INICIADO'
                      AND clock_timestamp() - fecha_inicio >= make_interval(secs => %s)
                    """,
                    (segundos,),
                )
                return cur.rowcount


def recuperar_reembolsos_sin_enviar(segundos: int = PLAZO_PASARELA_SEGUNDOS) -> int:
    with pool.connection() as conn:
        conn.execute(
            """
            UPDATE intento_pago i
            SET estado = 'REEMBOLSO_EN_VERIFICACION'
            FROM reembolso r
            WHERE r.reembolso_id = i.reembolso_actual_id
              AND i.estado = 'REEMBOLSO_EN_PROCESO'
              AND r.fecha_envio IS NOT NULL
              AND r.fecha_confirmacion IS NULL
              AND clock_timestamp() - r.fecha_envio >= make_interval(secs => %s)
            """,
            (segundos,),
        )
        conn.commit()
        pendientes = conn.execute(
            """
            SELECT r.reembolso_id
            FROM intento_pago i
            JOIN reembolso r ON r.reembolso_id = i.reembolso_actual_id
            WHERE i.estado = 'REEMBOLSO_EN_PROCESO'
              AND r.fecha_envio IS NULL
              AND clock_timestamp() - r.fecha_solicitud >= make_interval(secs => %s)
            ORDER BY r.fecha_solicitud
            """,
            (segundos,),
        ).fetchall()
    for (reembolso_id,) in pendientes:
        enviar_reembolso(reembolso_id)
    return len(pendientes)


# ==============================================================================
# CL-04: Iniciar un intento de pago
# ==============================================================================

class IniciarPagoSolicitud(BaseModel):
    monto: str


OTRO_CUERPO = "La Clave-Operacion ya fue utilizada con otro monto o cuerpo."


def _conflicto_de_clave(mensaje: str = OTRO_CUERPO) -> ErrorDeNegocio:
    return ErrorDeNegocio(409, "CONFLICTO_DE_CLAVE", mensaje)


def _marcar_en_verificacion(conn, intento_id: UUID) -> str:
    conn.execute("UPDATE intento_pago SET estado = 'EN_VERIFICACION' WHERE intento_id = %s AND estado = 'INICIADO'",
                 (intento_id,))
    return conn.execute("SELECT estado FROM intento_pago WHERE intento_id = %s", (intento_id,)).fetchone()[0]


def _respuesta_de_intento(conn, intento_id: UUID, estado: str, estado_http: int) -> dict[str, Any]:
    pedido_id, monto, t_vence = conn.execute(
        "SELECT i.pedido_id, i.monto, r.t_vence FROM intento_pago i JOIN pedido p ON p.pedido_id = i.pedido_id "
        "JOIN reserva r ON r.reserva_id = p.reserva_id WHERE i.intento_id = %s",
        (intento_id,),
    ).fetchone()
    return {
        "estado_http": estado_http,
        "cuerpo": _serializar_intento(intento_id=intento_id, pedido_id=pedido_id, monto=monto, estado=estado,
                                      t_vence_reserva=t_vence),
        "estado_anterior": {"estado": "INICIADO"},
        "estado_resultante": {"estado": estado},
    }


def _guardar_cl04(conn, clave: UUID, huella: bytes, intento_id: UUID, trabajo):
    pedido_id = conn.execute("SELECT pedido_id FROM intento_pago WHERE intento_id = %s", (intento_id,)).fetchone()[0]
    return ejecutar_idempotente(conn=conn, clave=clave, operacion="CL-04", objeto="pedido", objeto_id=pedido_id,
                                actor_tipo="CLIENTE", huella=huella, trabajo=trabajo)


def _responder_intento_sin_respuesta(intento_id: UUID, clave: UUID, huella: bytes):
    with pool.connection() as conn:
        def trabajo():
            estado = _marcar_en_verificacion(conn, intento_id)
            return _respuesta_de_intento(conn, intento_id, estado, 202 if estado in ("INICIADO", "EN_VERIFICACION") else 201)

        respuesta, _ = _guardar_cl04(conn, clave, huella, intento_id, trabajo)
        conn.commit()
    return respuesta, True


def _respuesta_guardada(clave: UUID, huella: bytes) -> dict | None:
    with pool.connection() as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s::text, 0))", (str(clave),))
        previa = conn.execute("SELECT huella_solicitud, resultado, respuesta FROM registro_operacion WHERE clave_operacion = %s",
                              (clave,)).fetchone()
    if previa is None:
        return None
    huella_guardada, resultado, respuesta = previa
    if bytes(huella_guardada) != huella:
        raise _conflicto_de_clave()
    if resultado == "RECHAZADA":
        error = respuesta.get("cuerpo", {})
        raise ErrorDeNegocio(respuesta.get("estado_http", 400), error.get("codigo", "SOLICITUD_INVALIDA"),
                             error.get("mensaje", "Solicitud rechazada previamente."), error.get("detalle"))
    return respuesta


def _intento_sin_respuesta_guardada(clave: UUID, huella: bytes) -> UUID | None:
    with pool.connection() as conn:
        fila = conn.execute("SELECT intento_id, huella_solicitud FROM intento_pago WHERE clave_operacion = %s",
                            (clave,)).fetchone()
    if fila is not None and bytes(fila[1]) != huella:
        raise _conflicto_de_clave()
    return fila[0] if fila else None


def _validar_pedido_para_pago(conn, pedido_id: UUID, cliente: UUID, monto: Decimal) -> None:
    fila = conn.execute("SELECT reserva_id, cliente_id, total_vigente, estado FROM pedido WHERE pedido_id = %s FOR UPDATE",
                        (pedido_id,)).fetchone()
    if fila is None or fila[1] != cliente:
        raise ErrorDeNegocio(404, "NO_ENCONTRADO", "El pedido no existe o no pertenece al cliente.")
    reserva_id, _, total_vigente, estado = fila
    if estado != "PENDIENTE_DE_PAGO":
        raise ErrorDeNegocio(409, "ESTADO_INCOMPATIBLE", f"El pedido está en estado {estado} y no admite pagos.")
    exigir_reserva_vigente(conn, reserva_id)
    if monto != total_vigente:
        raise ErrorDeNegocio(400, "SOLICITUD_INVALIDA", "El monto del intento debe coincidir con el total vigente del pedido.")


def _crear_intento(pedido_id: UUID, cliente: UUID, monto: Decimal, clave: UUID, huella: bytes) -> UUID:
    intento_id = uuid4()
    with pool.connection() as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s::text, 0))", (str(clave),))
        if conn.execute("SELECT 1 FROM registro_operacion WHERE clave_operacion = %s", (clave,)).fetchone():
            raise _conflicto_de_clave("La Clave-Operacion ya fue utilizada por otra operación.")
        _validar_pedido_para_pago(conn, pedido_id, cliente, monto)
        try:
            with conn.transaction():
                conn.execute(
                    "INSERT INTO intento_pago (intento_id, pedido_id, monto, fecha_inicio, estado, clave_operacion, huella_solicitud) "
                    "VALUES (%s, %s, %s, clock_timestamp(), 'INICIADO', %s, %s)",
                    (intento_id, pedido_id, monto, clave, huella),
                )
        except UniqueViolation as exc:
            raise ErrorDeNegocio(409, "INTENTO_ABIERTO", "Ya existe un intento de pago en curso para este pedido.") from exc
    return intento_id


def _cerrar_intento(intento_id: UUID, clave: UUID, huella: bytes, resultado_pasarela) -> tuple[dict, bool]:
    with pool.connection() as conn:
        def trabajo():
            if resultado_pasarela is None:
                return _respuesta_de_intento(conn, intento_id, _marcar_en_verificacion(conn, intento_id), 202)
            return _respuesta_de_intento(conn, intento_id, aplicar_resultado_intento(conn, intento_id, *resultado_pasarela), 201)

        return _guardar_cl04(conn, clave, huella, intento_id, trabajo)


def _confirmar_si_aprobado(respuesta: dict) -> None:
    if respuesta["cuerpo"]["estado"] == "APROBADO":
        procesar_eventos_pendientes()


def _cobrar(pedido_id: UUID, cliente: UUID, monto: Decimal, clave: UUID, huella: bytes) -> tuple[dict, bool]:
    guardada = _respuesta_guardada(clave, huella)
    if guardada is not None:
        return guardada, True
    pendiente = _intento_sin_respuesta_guardada(clave, huella)
    if pendiente is not None:
        return _responder_intento_sin_respuesta(pendiente, clave, huella)
    intento_id = _ejecutar_con_reintento_concurrencia(lambda: _crear_intento(pedido_id, cliente, monto, clave, huella))
    resultado = pasarela_simulada.cobrar(intento_id, _formatear_dinero(monto))
    respuesta, previa = _ejecutar_con_reintento_concurrencia(lambda: _cerrar_intento(intento_id, clave, huella, resultado))
    _confirmar_si_aprobado(respuesta)
    return respuesta, previa


def _iniciar_intento_pago_en(
    pedido_id: UUID,
    cuerpo: IniciarPagoSolicitud,
    response: Response,
    clave_operacion: str | None = Header(default=None, alias="Clave-Operacion"),
    cliente_id: str | None = Header(default=None, alias="Cliente-Id"),
):
    clave = validar_clave_operacion(clave_operacion)
    cliente = _parsear_uuid_cabecera(cliente_id, "Cliente-Id")
    monto = _parsear_dinero(cuerpo.monto)
    huella = calcular_huella("CL-04", pedido_id, {"monto": _formatear_dinero(monto), "cliente_id": str(cliente)})
    respuesta, previa = _cobrar(pedido_id, cliente, monto, clave, huella)
    response.status_code = respuesta["estado_http"]
    if previa:
        response.headers["Resultado-Previo"] = "true"
    return respuesta["cuerpo"]


@contextmanager
def _cerrojo_de_clave(clave: UUID):
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(hashtextextended(%s::text, 1))", (str(clave),))
        try:
            yield
        finally:
            conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s::text, 1))", (str(clave),))


@router.post("/v1/pedidos/{pedido_id}/intentos-pago")
def iniciar_intento_pago(
    pedido_id: UUID,
    cuerpo: IniciarPagoSolicitud,
    response: Response,
    clave_operacion: str | None = Header(default=None, alias="Clave-Operacion"),
    cliente_id: str | None = Header(default=None, alias="Cliente-Id"),
):
    clave = validar_clave_operacion(clave_operacion)
    with _cerrojo_de_clave(clave):
        return _iniciar_intento_pago_en(pedido_id, cuerpo, response, clave_operacion, cliente_id)


# ==============================================================================
# CL-05: Notificación de la pasarela (AT-6)
# ==============================================================================

RESULTADOS_POR_OBJETO = {"INTENTO": ("APROBADO", "RECHAZADO"), "REEMBOLSO": ("CONFIRMADO", "FALLIDO")}


class NotificacionPasarelaSolicitud(BaseModel):
    objeto: Literal["INTENTO", "REEMBOLSO"]
    objeto_id: UUID
    referencia_externa: str = Field(pattern=r"\S")
    resultado: str
    detalle: dict[str, Any] = Field(default_factory=dict)


def validar_resultado_notificacion(cuerpo: NotificacionPasarelaSolicitud) -> None:
    if cuerpo.resultado not in RESULTADOS_POR_OBJETO[cuerpo.objeto]:
        raise ErrorDeNegocio(400, "SOLICITUD_INVALIDA", f"Resultado {cuerpo.resultado} incompatible con objeto {cuerpo.objeto}.")


def notificacion_registrada(conn, cuerpo: NotificacionPasarelaSolicitud):
    fila = conn.execute(
        "SELECT resultado_aplicado FROM notificacion_pasarela WHERE objeto = %s AND objeto_id = %s AND referencia_externa = %s",
        (cuerpo.objeto, cuerpo.objeto_id, cuerpo.referencia_externa),
    ).fetchone()
    return fila[0] if fila else None


def notificar_intento(conn, cuerpo: NotificacionPasarelaSolicitud) -> tuple[str, str, dict]:
    fila = conn.execute("SELECT estado FROM intento_pago WHERE intento_id = %s", (cuerpo.objeto_id,)).fetchone()
    if fila is None:
        raise ErrorDeNegocio(404, "NO_ENCONTRADO", "Intento de pago no encontrado.")
    estado = aplicar_resultado_intento(conn, cuerpo.objeto_id, cuerpo.resultado, cuerpo.referencia_externa)
    return fila[0], estado, {"procesar_eventos": estado == "APROBADO"}


def notificar_reembolso(conn, cuerpo: NotificacionPasarelaSolicitud) -> tuple[str, str, dict]:
    fila = conn.execute(
        "SELECT i.estado, i.intento_id FROM reembolso r JOIN intento_pago i ON i.intento_id = r.intento_id WHERE r.reembolso_id = %s",
        (cuerpo.objeto_id,),
    ).fetchone()
    if fila is None:
        raise ErrorDeNegocio(404, "NO_ENCONTRADO", "Reembolso no encontrado.")
    estado = _aplicar_resultado_reembolso(conn, cuerpo.objeto_id, cuerpo.resultado, cuerpo.referencia_externa)
    return fila[0], estado, {"enviar_reembolso": _siguiente_en_cola(conn, fila[1], estado)}


APLICAR_NOTIFICACION = {"INTENTO": notificar_intento, "REEMBOLSO": notificar_reembolso}


def guardar_notificacion(conn, cuerpo: NotificacionPasarelaSolicitud, clave: UUID, huella: bytes,
                         estado_previo: str, respuesta: dict) -> None:
    conn.execute(
        "INSERT INTO notificacion_pasarela (objeto, intento_id, reembolso_id, objeto_id, referencia_externa, "
        "resultado_informado, fecha_recepcion, resultado_aplicado) VALUES (%s, %s, %s, %s, %s, %s, clock_timestamp(), %s)",
        (cuerpo.objeto, cuerpo.objeto_id if cuerpo.objeto == "INTENTO" else None,
         cuerpo.objeto_id if cuerpo.objeto == "REEMBOLSO" else None, cuerpo.objeto_id, cuerpo.referencia_externa,
         Jsonb({"resultado": cuerpo.resultado, "detalle": cuerpo.detalle}), Jsonb(respuesta)),
    )
    conn.execute(
        "INSERT INTO registro_operacion (registro_id, clave_operacion, operacion, objeto, objeto_id, huella_solicitud, "
        "estado_anterior, estado_resultante, actor_tipo, usuario_id, fecha, resultado, respuesta, motivo_rechazo) "
        "VALUES (%s, %s, 'CL-05', %s, %s, %s, %s, %s, 'PASARELA', NULL, clock_timestamp(), 'APLICADA', %s, NULL)",
        (uuid4(), clave, cuerpo.objeto.lower(), cuerpo.objeto_id, huella, Jsonb({"estado": estado_previo}),
         Jsonb({"estado": respuesta["estado_aplicado"]}), Jsonb({"estado_http": 200, "cuerpo": respuesta})),
    )


def aplicar_notificacion(cuerpo: NotificacionPasarelaSolicitud, clave: UUID, huella: bytes) -> tuple[dict, bool, dict]:
    with pool.connection() as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s::text, 0))", (str(clave),))
        previa = notificacion_registrada(conn, cuerpo)
        if previa is not None:
            return previa, True, {}
        estado_previo, estado, despues = APLICAR_NOTIFICACION[cuerpo.objeto](conn, cuerpo)
        respuesta = {"objeto": cuerpo.objeto, "objeto_id": str(cuerpo.objeto_id), "estado_aplicado": estado}
        guardar_notificacion(conn, cuerpo, clave, huella, estado_previo, respuesta)
        return respuesta, False, despues


def despues_de_notificar(despues: dict) -> None:
    if despues.get("procesar_eventos"):
        procesar_eventos_pendientes()
    if despues.get("enviar_reembolso"):
        enviar_reembolso(despues["enviar_reembolso"])


@router.post("/v1/pasarela/notificaciones")
def recibir_notificacion_pasarela(cuerpo: NotificacionPasarelaSolicitud, response: Response):
    validar_resultado_notificacion(cuerpo)
    clave = uuid5(NAMESPACE_URL, f"{cuerpo.objeto}:{cuerpo.objeto_id}:{cuerpo.referencia_externa}")
    huella = calcular_huella("CL-05", cuerpo.objeto_id, {"objeto": cuerpo.objeto, "referencia_externa": cuerpo.referencia_externa,
                                                       "resultado": cuerpo.resultado})
    respuesta, previa, despues = _ejecutar_con_reintento_concurrencia(lambda: aplicar_notificacion(cuerpo, clave, huella))
    despues_de_notificar(despues)
    if previa:
        response.headers["Resultado-Previo"] = "true"
    return respuesta


# ==============================================================================
# CL-06: Resolver con evidencia (revisión manual)
# ==============================================================================

class VerificacionIntentoSolicitud(BaseModel):
    resultado: Literal["APROBADO", "RECHAZADO"]
    evidencia_fuente: str
    evidencia: dict[str, Any]


def _intento_en_verificacion(conn, intento_id: UUID) -> tuple:
    fila = conn.execute(
        "SELECT i.estado FROM intento_pago i WHERE i.intento_id = %s FOR UPDATE", (intento_id,)).fetchone()
    if fila is None:
        raise ErrorDeNegocio(404, "NO_ENCONTRADO", "El intento de pago no existe.")
    if fila[0] != "EN_VERIFICACION":
        raise ErrorDeNegocio(409, "ESTADO_INCOMPATIBLE",
                             f"El intento está en {fila[0]}; solo se puede verificar desde EN_VERIFICACION.")


def _resolver_intento_manual(conn, intento_id: UUID, usuario: UUID, cuerpo) -> dict[str, Any]:
    _exigir_rol_responsable(conn, usuario)
    _intento_en_verificacion(conn, intento_id)
    conn.execute("UPDATE intento_pago SET evidencia_fuente = %s, evidencia = %s, evidencia_responsable = %s WHERE intento_id = %s",
                 (cuerpo.evidencia_fuente, Jsonb(cuerpo.evidencia), usuario, intento_id))
    respuesta = _respuesta_de_intento(conn, intento_id, aplicar_resultado_intento(conn, intento_id, cuerpo.resultado), 200)
    return respuesta | {"estado_anterior": {"estado": "EN_VERIFICACION"}}


def _operar_cl(operacion: str, objeto: str, objeto_id: UUID, clave: UUID, usuario: UUID, huella: bytes, trabajo,
               response: Response) -> dict:
    def transaccion():
        with pool.connection() as conn:
            return ejecutar_idempotente(conn=conn, clave=clave, operacion=operacion, objeto=objeto, objeto_id=objeto_id,
                                        actor_tipo="USUARIO_OPERATIVO", huella=huella, usuario_id=usuario,
                                        trabajo=lambda: trabajo(conn))

    respuesta, previa = _ejecutar_con_reintento_concurrencia(transaccion)
    response.status_code = respuesta["estado_http"]
    if previa:
        response.headers["Resultado-Previo"] = "true"
    return respuesta


@router.post("/v1/intentos-pago/{intento_id}/verificacion")
def verificar_intento_manual(
    intento_id: UUID,
    cuerpo: VerificacionIntentoSolicitud,
    response: Response,
    clave_operacion: str | None = Header(default=None, alias="Clave-Operacion"),
    usuario_id: str | None = Header(default=None, alias="Usuario-Id"),
):
    clave = validar_clave_operacion(clave_operacion)
    usuario = _parsear_uuid_cabecera(usuario_id, "Usuario-Id")
    huella = calcular_huella("CL-06", intento_id, {"tipo": "INTENTO", "usuario_id": str(usuario), "resultado": cuerpo.resultado,
                                                   "evidencia_fuente": cuerpo.evidencia_fuente, "evidencia": cuerpo.evidencia})
    respuesta = _operar_cl("CL-06", "intento_pago", intento_id, clave, usuario, huella,
                           lambda conn: _resolver_intento_manual(conn, intento_id, usuario, cuerpo), response)
    _confirmar_si_aprobado(respuesta)
    return respuesta["cuerpo"]


class VerificacionReembolsoSolicitud(BaseModel):
    resultado: Literal["CONFIRMADO", "FALLIDO"]
    evidencia: dict[str, Any]


def _reembolso_en_verificacion(conn, reembolso_id: UUID) -> tuple:
    fila = conn.execute(
        "SELECT r.intento_id, r.pedido_id, r.monto, r.motivo, r.referencia_externa, i.estado, i.reembolso_actual_id "
        "FROM reembolso r JOIN intento_pago i ON i.intento_id = r.intento_id WHERE r.reembolso_id = %s FOR UPDATE OF i, r",
        (reembolso_id,),
    ).fetchone()
    if fila is None:
        raise ErrorDeNegocio(404, "NO_ENCONTRADO", "El reembolso no existe.")
    if fila[5] != "REEMBOLSO_EN_VERIFICACION" or fila[6] != reembolso_id:
        raise ErrorDeNegocio(409, "ESTADO_INCOMPATIBLE",
                             f"El reembolso no está en REEMBOLSO_EN_VERIFICACION (estado actual: {fila[5]}).")
    return fila[:6]


def _resolver_reembolso_manual(conn, reembolso_id: UUID, usuario: UUID, cuerpo, siguiente: list) -> dict[str, Any]:
    _exigir_rol_responsable(conn, usuario)
    intento_id, pedido_id, monto, motivo, referencia, estado_previo = _reembolso_en_verificacion(conn, reembolso_id)
    conn.execute("UPDATE reembolso SET evidencia_revision = %s, responsable_revision = %s WHERE reembolso_id = %s",
                 (Jsonb(cuerpo.evidencia), usuario, reembolso_id))
    estado = _aplicar_resultado_reembolso(conn, reembolso_id=reembolso_id, resultado=cuerpo.resultado)
    siguiente.append(_siguiente_en_cola(conn, intento_id, estado))
    return {
        "estado_http": 200,
        "cuerpo": _serializar_reembolso(reembolso_id=reembolso_id, intento_id=intento_id, pedido_id=pedido_id, monto=monto,
                                        motivo=motivo, estado_intento=estado, referencia_externa=referencia),
        "estado_anterior": {"estado": estado_previo},
        "estado_resultante": {"estado": estado},
    }


@router.post("/v1/reembolsos/{reembolso_id}/verificacion")
def verificar_reembolso_manual(
    reembolso_id: UUID,
    cuerpo: VerificacionReembolsoSolicitud,
    response: Response,
    clave_operacion: str | None = Header(default=None, alias="Clave-Operacion"),
    usuario_id: str | None = Header(default=None, alias="Usuario-Id"),
):
    clave = validar_clave_operacion(clave_operacion)
    usuario = _parsear_uuid_cabecera(usuario_id, "Usuario-Id")
    huella = calcular_huella("CL-06", reembolso_id, {"tipo": "REEMBOLSO", "usuario_id": str(usuario),
                                                     "resultado": cuerpo.resultado, "evidencia": cuerpo.evidencia})
    siguiente: list = []
    respuesta = _operar_cl("CL-06", "reembolso", reembolso_id, clave, usuario, huella,
                           lambda conn: _resolver_reembolso_manual(conn, reembolso_id, usuario, cuerpo, siguiente), response)
    if siguiente and siguiente[-1]:
        enviar_reembolso(siguiente[-1])
    return respuesta["cuerpo"]


# ==============================================================================
# CL-17: Reintentar un reembolso
# ==============================================================================

def _reembolso_fallido(conn, reembolso_id: UUID) -> tuple:
    fila = conn.execute(
        "SELECT r.intento_id, r.pedido_id, r.monto, r.motivo, r.referencia_externa, i.estado, i.reembolso_actual_id "
        "FROM reembolso r JOIN intento_pago i ON i.intento_id = r.intento_id WHERE r.reembolso_id = %s FOR UPDATE OF i, r",
        (reembolso_id,),
    ).fetchone()
    if fila is None:
        raise ErrorDeNegocio(404, "NO_ENCONTRADO", "El reembolso no existe.")
    if fila[5] != "REEMBOLSO_FALLIDO" or fila[6] != reembolso_id:
        raise ErrorDeNegocio(409, "ESTADO_INCOMPATIBLE",
                             f"Solo se puede reintentar desde REEMBOLSO_FALLIDO (estado actual: {fila[5]}).")
    return fila[:5]


def _preparar_reintento(conn, reembolso_id: UUID, usuario: UUID) -> dict[str, Any]:
    _exigir_rol_responsable(conn, usuario)
    intento_id, pedido_id, monto, motivo, referencia = _reembolso_fallido(conn, reembolso_id)
    conn.execute("UPDATE reembolso SET fecha_envio = NULL, fecha_fallo_confirmado = NULL WHERE reembolso_id = %s", (reembolso_id,))
    conn.execute("UPDATE intento_pago SET estado = 'REEMBOLSO_EN_PROCESO' WHERE intento_id = %s", (intento_id,))
    return {
        "estado_http": 202,
        "cuerpo": _serializar_reembolso(reembolso_id=reembolso_id, intento_id=intento_id, pedido_id=pedido_id, monto=monto,
                                        motivo=motivo, estado_intento="REEMBOLSO_EN_PROCESO", referencia_externa=referencia),
        "estado_anterior": {"estado": "REEMBOLSO_FALLIDO"},
        "estado_resultante": {"estado": "REEMBOLSO_EN_PROCESO"},
    }


@router.post("/v1/reembolsos/{reembolso_id}/reintentos")
def reintentar_reembolso(
    reembolso_id: UUID,
    response: Response,
    clave_operacion: str | None = Header(default=None, alias="Clave-Operacion"),
    usuario_id: str | None = Header(default=None, alias="Usuario-Id"),
):
    clave = validar_clave_operacion(clave_operacion)
    usuario = _parsear_uuid_cabecera(usuario_id, "Usuario-Id")
    huella = calcular_huella("CL-17", reembolso_id, {"usuario_id": str(usuario)})
    respuesta = _operar_cl("CL-17", "reembolso", reembolso_id, clave, usuario, huella,
                           lambda conn: _preparar_reintento(conn, reembolso_id, usuario), response)
    enviar_reembolso(reembolso_id)
    return respuesta["cuerpo"]
