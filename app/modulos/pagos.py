from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal, InvalidOperation
import time
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, Response
from psycopg.errors import DeadlockDetected, SerializationFailure, UniqueViolation
from psycopg.types.json import Jsonb
from pydantic import BaseModel

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


def aplicar_resultado_intento(
    conn,
    intento_id: UUID,
    resultado: str,
    referencia_externa: str | None = None,
) -> tuple[str, bool]:
    """
    Función única para resolver un intento de pago (usada por CL-04, CL-05 y CL-06).
    Devuelve (estado_resultante, genero_ev01).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT estado
            FROM intento_pago
            WHERE intento_id = %s
            FOR UPDATE
            """,
            (intento_id,),
        )
        fila = cur.fetchone()
        if fila is None:
            raise ErrorDeNegocio(404, "NO_ENCONTRADO", "El intento de pago no existe.")

        estado_actual = fila[0]
        if estado_actual not in ("INICIADO", "EN_VERIFICACION"):
            return estado_actual, False

        cur.execute(
            """
            UPDATE intento_pago
            SET estado = %s,
                referencia_externa = COALESCE(%s, referencia_externa),
                fecha_resultado = clock_timestamp()
            WHERE intento_id = %s
              AND estado IN ('INICIADO', 'EN_VERIFICACION')
            RETURNING estado
            """,
            (resultado, referencia_externa, intento_id),
        )
        actualizado = cur.fetchone()
        if actualizado is None:
            cur.execute("SELECT estado FROM intento_pago WHERE intento_id = %s", (intento_id,))
            return cur.fetchone()[0], False

        nuevo_estado = actualizado[0]
        genero_ev01 = False
        if nuevo_estado == "APROBADO":
            cur.execute(
                """
                INSERT INTO evento_interno (
                    evento_id, tipo, productor, intento_id, fecha, procesado
                )
                VALUES (
                    gen_random_uuid(), 'EV-01', 'M04', %s, clock_timestamp(), false
                )
                """,
                (intento_id,),
            )
            genero_ev01 = True

        return nuevo_estado, genero_ev01


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
        if reembolso_actual_id is not None and estado_intento in (
            "REEMBOLSO_EN_PROCESO",
            "REEMBOLSO_EN_VERIFICACION",
            "REEMBOLSO_FALLIDO",
        ):
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


def enviar_reembolso(reembolso_id: UUID) -> None:
    """
    Envía el reembolso a la pasarela simulada FUERA de la transacción de creación.
    Si responde CONFIRMADO/FALLIDO, actualiza; si no responde (None), pasa a REEMBOLSO_EN_VERIFICACION.
    """
    monto_str: str | None = None
    siguiente_a_enviar: UUID | None = None

    with pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT r.reembolso_id, r.intento_id, r.monto, r.fecha_envio,
                           i.estado, i.reembolso_actual_id
                    FROM reembolso r
                    JOIN intento_pago i ON i.intento_id = r.intento_id
                    WHERE r.reembolso_id = %s
                    FOR UPDATE OF i, r
                    """,
                    (reembolso_id,),
                )
                fila = cur.fetchone()
                if fila is None:
                    return
                _, _, monto, fecha_envio, estado_intento, reembolso_actual_id = fila

                # Nunca reenviar si ya fue enviado o ya no es el reembolso activo
                if fecha_envio is not None or reembolso_actual_id != reembolso_id:
                    return
                if estado_intento != "REEMBOLSO_EN_PROCESO":
                    return

                cur.execute(
                    """
                    UPDATE reembolso
                    SET fecha_envio = clock_timestamp()
                    WHERE reembolso_id = %s
                    """,
                    (reembolso_id,),
                )
                monto_str = _formatear_dinero(monto)

    if monto_str is None:
        return

    respuesta_pasarela = pasarela_simulada.reembolsar(reembolso_id, monto_str)

    with pool.connection() as conn:
        with conn.transaction():
            if respuesta_pasarela is None:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE intento_pago
                        SET estado = 'REEMBOLSO_EN_VERIFICACION'
                        WHERE reembolso_actual_id = %s
                          AND estado = 'REEMBOLSO_EN_PROCESO'
                        """,
                        (reembolso_id,),
                    )
                return

            resultado, referencia = respuesta_pasarela
            nuevo_estado = _aplicar_resultado_reembolso(
                conn,
                reembolso_id=reembolso_id,
                resultado=resultado,
                referencia_externa=referencia,
            )
            if nuevo_estado == "REEMBOLSO_EN_PROCESO":
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT reembolso_actual_id
                        FROM intento_pago
                        WHERE reembolso_actual_id IS NOT NULL
                          AND intento_id = (SELECT intento_id FROM reembolso WHERE reembolso_id = %s)
                        """,
                        (reembolso_id,),
                    )
                    fila_sig = cur.fetchone()
                    if fila_sig:
                        siguiente_a_enviar = fila_sig[0]

    if siguiente_a_enviar is not None:
        enviar_reembolso(siguiente_a_enviar)


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


def _iniciar_intento_pago_en(
    pedido_id: UUID,
    cuerpo: IniciarPagoSolicitud,
    response: Response,
    clave_operacion: str | None = Header(default=None, alias="Clave-Operacion"),
    cliente_id: str | None = Header(default=None, alias="Cliente-Id"),
):
    clave = validar_clave_operacion(clave_operacion)
    cliente_uuid = _parsear_uuid_cabecera(cliente_id, "Cliente-Id")
    monto_decimal = _parsear_dinero(cuerpo.monto)
    huella = calcular_huella(
        "CL-04",
        pedido_id,
        {"monto": _formatear_dinero(monto_decimal), "cliente_id": str(cliente_uuid)},
    )

    # 1. Verificación rápida de clave previa antes de crear intento
    with pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s::text, 0))",
                    (str(clave),),
                )
                cur.execute(
                    """
                    SELECT huella_solicitud, resultado, respuesta
                    FROM registro_operacion
                    WHERE clave_operacion = %s
                    """,
                    (clave,),
                )
                previa = cur.fetchone()
                if previa is not None:
                    huella_guardada, resultado_guardado, respuesta_guardada = previa
                    if bytes(huella_guardada) != huella:
                        raise ErrorDeNegocio(
                            409,
                            "CONFLICTO_DE_CLAVE",
                            "La Clave-Operacion ya fue utilizada con otro monto o cuerpo.",
                        )
                    if resultado_guardado == "RECHAZADA":
                        cuerpo_err = respuesta_guardada.get("cuerpo", {})
                        raise ErrorDeNegocio(
                            respuesta_guardada.get("estado_http", 400),
                            cuerpo_err.get("codigo", "SOLICITUD_INVALIDA"),
                            cuerpo_err.get("mensaje", "Solicitud rechazada previamente."),
                            cuerpo_err.get("detalle"),
                        )
                    response.status_code = respuesta_guardada["estado_http"]
                    response.headers["Resultado-Previo"] = "true"
                    return respuesta_guardada["cuerpo"]

    # 2. Transacción 1: validar pedido/reserva e insertar intento en INICIADO
    intento_id = uuid4()
    t_vence_reserva: datetime | None = None

    def _tx1_iniciar() -> None:
        nonlocal t_vence_reserva
        with pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT pedido_id, reserva_id, cliente_id, total_vigente, estado
                        FROM pedido
                        WHERE pedido_id = %s
                        FOR UPDATE
                        """,
                        (pedido_id,),
                    )
                    fila_ped = cur.fetchone()
                    if fila_ped is None or fila_ped[2] != cliente_uuid:
                        raise ErrorDeNegocio(
                            404,
                            "NO_ENCONTRADO",
                            "El pedido no existe o no pertenece al cliente.",
                        )

                    _, reserva_id, _, total_vigente, estado_pedido = fila_ped
                    if estado_pedido != "PENDIENTE_DE_PAGO":
                        raise ErrorDeNegocio(
                            409,
                            "ESTADO_INCOMPATIBLE",
                            f"El pedido está en estado {estado_pedido} y no admite pagos.",
                        )

                    exigir_reserva_vigente(conn, reserva_id)

                    if monto_decimal != total_vigente:
                        raise ErrorDeNegocio(
                            400,
                            "SOLICITUD_INVALIDA",
                            "El monto del intento debe coincidir con el total vigente del pedido.",
                        )

                    cur.execute(
                        "SELECT t_vence FROM reserva WHERE reserva_id = %s",
                        (reserva_id,),
                    )
                    t_vence_reserva = cur.fetchone()[0]

                    try:
                        cur.execute(
                            """
                            INSERT INTO intento_pago (
                                intento_id, pedido_id, monto, fecha_inicio, estado
                            )
                            VALUES (
                                %s, %s, %s, clock_timestamp(), 'INICIADO'
                            )
                            """,
                            (intento_id, pedido_id, monto_decimal),
                        )
                    except UniqueViolation as exc:
                        raise ErrorDeNegocio(
                            409,
                            "INTENTO_ABIERTO",
                            "Ya existe un intento de pago en curso para este pedido.",
                        ) from exc

    _ejecutar_con_reintento_concurrencia(_tx1_iniciar)

    # 3. Llamada externa fuera de la transacción
    resultado_pasarela = pasarela_simulada.cobrar(intento_id, _formatear_dinero(monto_decimal))

    # 4. Transacción 2: aplicar resultado y guardar registro_operacion idempotente
    genero_ev01 = False

    def _tx2_cerrar() -> tuple[dict[str, Any], bool]:
        nonlocal genero_ev01
        with pool.connection() as conn:

            def _trabajo() -> dict[str, Any]:
                nonlocal genero_ev01
                assert t_vence_reserva is not None
                if resultado_pasarela is None:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE intento_pago
                            SET estado = 'EN_VERIFICACION'
                            WHERE intento_id = %s AND estado = 'INICIADO'
                            RETURNING estado
                            """,
                            (intento_id,),
                        )
                        fila_est = cur.fetchone()
                        if fila_est is None:
                            cur.execute(
                                "SELECT estado FROM intento_pago WHERE intento_id = %s",
                                (intento_id,),
                            )
                            estado_final = cur.fetchone()[0]
                        else:
                            estado_final = fila_est[0]
                    estado_http = 202
                else:
                    res_str, ref_ext = resultado_pasarela
                    estado_final, genero_ev01 = aplicar_resultado_intento(
                        conn,
                        intento_id=intento_id,
                        resultado=res_str,
                        referencia_externa=ref_ext,
                    )
                    estado_http = 201

                cuerpo_resp = _serializar_intento(
                    intento_id=intento_id,
                    pedido_id=pedido_id,
                    monto=monto_decimal,
                    estado=estado_final,
                    t_vence_reserva=t_vence_reserva,
                )
                return {
                    "estado_http": estado_http,
                    "cuerpo": cuerpo_resp,
                    "estado_anterior": {"estado": "INICIADO"},
                    "estado_resultante": {"estado": estado_final},
                }

            return ejecutar_idempotente(
                conn=conn,
                clave=clave,
                operacion="CL-04",
                objeto="pedido",
                objeto_id=pedido_id,
                actor_tipo="CLIENTE",
                huella=huella,
                trabajo=_trabajo,
            )

    respuesta_final, es_previo = _ejecutar_con_reintento_concurrencia(_tx2_cerrar)

    if genero_ev01 and not es_previo:
        procesar_eventos_pendientes()

    response.status_code = respuesta_final["estado_http"]
    if es_previo:
        response.headers["Resultado-Previo"] = "true"
    return respuesta_final["cuerpo"]


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

class NotificacionPasarelaSolicitud(BaseModel):
    objeto: Literal["INTENTO", "REEMBOLSO"]
    objeto_id: UUID
    referencia_externa: str
    resultado: str
    detalle: dict[str, Any] | None = None


@router.post("/v1/pasarela/notificaciones")
def recibir_notificacion_pasarela(
    cuerpo: NotificacionPasarelaSolicitud,
    response: Response,
):
    if not cuerpo.referencia_externa or not cuerpo.referencia_externa.strip():
        raise ErrorDeNegocio(400, "SOLICITUD_INVALIDA", "referencia_externa es obligatoria.")

    if cuerpo.objeto == "INTENTO" and cuerpo.resultado not in ("APROBADO", "RECHAZADO"):
        raise ErrorDeNegocio(
            400,
            "SOLICITUD_INVALIDA",
            f"Resultado {cuerpo.resultado} incompatible con objeto INTENTO.",
        )
    if cuerpo.objeto == "REEMBOLSO" and cuerpo.resultado not in ("CONFIRMADO", "FALLIDO"):
        raise ErrorDeNegocio(
            400,
            "SOLICITUD_INVALIDA",
            f"Resultado {cuerpo.resultado} incompatible con objeto REEMBOLSO.",
        )

    clave_derivada = uuid4()
    import uuid as _uuid

    clave_derivada = _uuid.uuid5(
        _uuid.NAMESPACE_URL,
        f"{cuerpo.objeto}:{cuerpo.objeto_id}:{cuerpo.referencia_externa}",
    )
    huella = calcular_huella(
        "CL-05",
        cuerpo.objeto_id,
        {
            "objeto": cuerpo.objeto,
            "referencia_externa": cuerpo.referencia_externa,
            "resultado": cuerpo.resultado,
        },
    )

    genero_ev01 = False
    siguiente_reembolso: UUID | None = None

    def _tx_notificacion() -> tuple[dict[str, Any], bool]:
        nonlocal genero_ev01, siguiente_reembolso
        with pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended(%s::text, 0))",
                        (str(clave_derivada),),
                    )

                    # Verificar primero si la notificación ya fue registrada
                    cur.execute(
                        """
                        SELECT resultado_aplicado
                        FROM notificacion_pasarela
                        WHERE objeto = %s
                          AND objeto_id = %s
                          AND referencia_externa = %s
                        """,
                        (cuerpo.objeto, cuerpo.objeto_id, cuerpo.referencia_externa),
                    )
                    existente = cur.fetchone()
                    if existente is not None:
                        return existente[0], True

                    # Verificar existencia del objeto destino
                    if cuerpo.objeto == "INTENTO":
                        cur.execute(
                            "SELECT estado FROM intento_pago WHERE intento_id = %s",
                            (cuerpo.objeto_id,),
                        )
                        fila_obj = cur.fetchone()
                        if fila_obj is None:
                            raise ErrorDeNegocio(404, "NO_ENCONTRADO", "Intento de pago no encontrado.")
                        estado_previo = fila_obj[0]

                        estado_aplicado, genero_ev01 = aplicar_resultado_intento(
                            conn,
                            intento_id=cuerpo.objeto_id,
                            resultado=cuerpo.resultado,
                            referencia_externa=cuerpo.referencia_externa,
                        )
                    else:
                        cur.execute(
                            """
                            SELECT i.estado, i.intento_id
                            FROM reembolso r
                            JOIN intento_pago i ON i.intento_id = r.intento_id
                            WHERE r.reembolso_id = %s
                            """,
                            (cuerpo.objeto_id,),
                        )
                        fila_obj = cur.fetchone()
                        if fila_obj is None:
                            raise ErrorDeNegocio(404, "NO_ENCONTRADO", "Reembolso no encontrado.")
                        estado_previo, intento_asociado = fila_obj

                        estado_aplicado = _aplicar_resultado_reembolso(
                            conn,
                            reembolso_id=cuerpo.objeto_id,
                            resultado=cuerpo.resultado,
                            referencia_externa=cuerpo.referencia_externa,
                        )
                        if estado_aplicado == "REEMBOLSO_EN_PROCESO":
                            cur.execute(
                                """
                                SELECT reembolso_actual_id
                                FROM intento_pago
                                WHERE intento_id = %s
                                """,
                                (intento_asociado,),
                            )
                            fila_sig = cur.fetchone()
                            if fila_sig:
                                siguiente_reembolso = fila_sig[0]

                    respuesta_payload = {
                        "objeto": cuerpo.objeto,
                        "objeto_id": str(cuerpo.objeto_id),
                        "estado_aplicado": estado_aplicado,
                    }
                    resultado_informado = {
                        "resultado": cuerpo.resultado,
                        "detalle": cuerpo.detalle or {},
                    }

                    intento_col = cuerpo.objeto_id if cuerpo.objeto == "INTENTO" else None
                    reembolso_col = cuerpo.objeto_id if cuerpo.objeto == "REEMBOLSO" else None

                    cur.execute(
                        """
                        INSERT INTO notificacion_pasarela (
                            objeto, intento_id, reembolso_id, objeto_id,
                            referencia_externa, resultado_informado,
                            fecha_recepcion, resultado_aplicado
                        )
                        VALUES (
                            %s, %s, %s, %s,
                            %s, %s,
                            clock_timestamp(), %s
                        )
                        ON CONFLICT (objeto, objeto_id, referencia_externa) DO NOTHING
                        RETURNING resultado_aplicado
                        """,
                        (
                            cuerpo.objeto,
                            intento_col,
                            reembolso_col,
                            cuerpo.objeto_id,
                            cuerpo.referencia_externa,
                            Jsonb(resultado_informado),
                            Jsonb(respuesta_payload),
                        ),
                    )
                    insertada = cur.fetchone()
                    if insertada is None:
                        cur.execute(
                            """
                            SELECT resultado_aplicado
                            FROM notificacion_pasarela
                            WHERE objeto = %s
                              AND objeto_id = %s
                              AND referencia_externa = %s
                            """,
                            (cuerpo.objeto, cuerpo.objeto_id, cuerpo.referencia_externa),
                        )
                        return cur.fetchone()[0], True

                    cur.execute(
                        """
                        INSERT INTO registro_operacion (
                            registro_id, clave_operacion, operacion, objeto, objeto_id,
                            huella_solicitud, estado_anterior, estado_resultante,
                            actor_tipo, usuario_id, fecha, resultado, respuesta, motivo_rechazo
                        )
                        VALUES (
                            %s, %s, 'CL-05', %s, %s,
                            %s, %s, %s,
                            'PASARELA', NULL, clock_timestamp(), 'APLICADA', %s, NULL
                        )
                        ON CONFLICT (clave_operacion) DO NOTHING
                        """,
                        (
                            uuid4(),
                            clave_derivada,
                            cuerpo.objeto.lower(),
                            cuerpo.objeto_id,
                            huella,
                            Jsonb({"estado": estado_previo}),
                            Jsonb({"estado": estado_aplicado}),
                            Jsonb({"estado_http": 200, "cuerpo": respuesta_payload}),
                        ),
                    )
                    return respuesta_payload, False

    cuerpo_respuesta, es_previo = _ejecutar_con_reintento_concurrencia(_tx_notificacion)

    if not es_previo:
        if genero_ev01:
            procesar_eventos_pendientes()
        if siguiente_reembolso is not None:
            enviar_reembolso(siguiente_reembolso)

    response.status_code = 200
    if es_previo:
        response.headers["Resultado-Previo"] = "true"
    return cuerpo_respuesta


# ==============================================================================
# CL-06: Resolver con evidencia (revisión manual)
# ==============================================================================

class VerificacionIntentoSolicitud(BaseModel):
    resultado: Literal["APROBADO", "RECHAZADO"]
    evidencia_fuente: str
    evidencia: dict[str, Any]


@router.post("/v1/intentos-pago/{intento_id}/verificacion")
def verificar_intento_manual(
    intento_id: UUID,
    cuerpo: VerificacionIntentoSolicitud,
    response: Response,
    clave_operacion: str | None = Header(default=None, alias="Clave-Operacion"),
    usuario_id: str | None = Header(default=None, alias="Usuario-Id"),
):
    clave = validar_clave_operacion(clave_operacion)
    usuario_uuid = _parsear_uuid_cabecera(usuario_id, "Usuario-Id")
    huella = calcular_huella(
        "CL-06",
        intento_id,
        {
            "tipo": "INTENTO",
            "usuario_id": str(usuario_uuid),
            "resultado": cuerpo.resultado,
            "evidencia_fuente": cuerpo.evidencia_fuente,
            "evidencia": cuerpo.evidencia,
        },
    )

    genero_ev01 = False

    def _tx_verif_intento() -> tuple[dict[str, Any], bool]:
        nonlocal genero_ev01
        with pool.connection() as conn:

            def _trabajo() -> dict[str, Any]:
                nonlocal genero_ev01
                _exigir_rol_responsable(conn, usuario_uuid)

                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT i.intento_id, i.pedido_id, i.monto, i.estado, r.t_vence
                        FROM intento_pago i
                        JOIN pedido p ON p.pedido_id = i.pedido_id
                        JOIN reserva r ON r.reserva_id = p.reserva_id
                        WHERE i.intento_id = %s
                        FOR UPDATE OF i
                        """,
                        (intento_id,),
                    )
                    fila = cur.fetchone()
                    if fila is None:
                        raise ErrorDeNegocio(404, "NO_ENCONTRADO", "El intento de pago no existe.")

                    _, pedido_id, monto, estado_actual, t_vence = fila
                    if estado_actual != "EN_VERIFICACION":
                        raise ErrorDeNegocio(
                            409,
                            "ESTADO_INCOMPATIBLE",
                            f"El intento está en {estado_actual}; solo se puede verificar desde EN_VERIFICACION.",
                        )

                    cur.execute(
                        """
                        UPDATE intento_pago
                        SET evidencia_fuente = %s,
                            evidencia = %s,
                            evidencia_responsable = %s
                        WHERE intento_id = %s
                        """,
                        (
                            cuerpo.evidencia_fuente,
                            Jsonb(cuerpo.evidencia),
                            usuario_uuid,
                            intento_id,
                        ),
                    )

                estado_final, genero_ev01 = aplicar_resultado_intento(
                    conn,
                    intento_id=intento_id,
                    resultado=cuerpo.resultado,
                )
                cuerpo_resp = _serializar_intento(
                    intento_id=intento_id,
                    pedido_id=pedido_id,
                    monto=monto,
                    estado=estado_final,
                    t_vence_reserva=t_vence,
                )
                return {
                    "estado_http": 200,
                    "cuerpo": cuerpo_resp,
                    "estado_anterior": {"estado": estado_actual},
                    "estado_resultante": {"estado": estado_final},
                }

            return ejecutar_idempotente(
                conn=conn,
                clave=clave,
                operacion="CL-06",
                objeto="intento_pago",
                objeto_id=intento_id,
                actor_tipo="USUARIO_OPERATIVO",
                huella=huella,
                trabajo=_trabajo,
                usuario_id=usuario_uuid,
            )

    respuesta_final, es_previo = _ejecutar_con_reintento_concurrencia(_tx_verif_intento)

    if genero_ev01 and not es_previo:
        procesar_eventos_pendientes()

    response.status_code = respuesta_final["estado_http"]
    if es_previo:
        response.headers["Resultado-Previo"] = "true"
    return respuesta_final["cuerpo"]


class VerificacionReembolsoSolicitud(BaseModel):
    resultado: Literal["CONFIRMADO", "FALLIDO"]
    evidencia: dict[str, Any]


@router.post("/v1/reembolsos/{reembolso_id}/verificacion")
def verificar_reembolso_manual(
    reembolso_id: UUID,
    cuerpo: VerificacionReembolsoSolicitud,
    response: Response,
    clave_operacion: str | None = Header(default=None, alias="Clave-Operacion"),
    usuario_id: str | None = Header(default=None, alias="Usuario-Id"),
):
    clave = validar_clave_operacion(clave_operacion)
    usuario_uuid = _parsear_uuid_cabecera(usuario_id, "Usuario-Id")
    huella = calcular_huella(
        "CL-06",
        reembolso_id,
        {
            "tipo": "REEMBOLSO",
            "usuario_id": str(usuario_uuid),
            "resultado": cuerpo.resultado,
            "evidencia": cuerpo.evidencia,
        },
    )

    siguiente_reembolso: UUID | None = None

    def _tx_verif_reembolso() -> tuple[dict[str, Any], bool]:
        nonlocal siguiente_reembolso
        with pool.connection() as conn:

            def _trabajo() -> dict[str, Any]:
                nonlocal siguiente_reembolso
                _exigir_rol_responsable(conn, usuario_uuid)

                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT r.reembolso_id, r.intento_id, r.pedido_id, r.monto, r.motivo,
                               r.referencia_externa, i.estado, i.reembolso_actual_id
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
                        pedido_id,
                        monto,
                        motivo,
                        referencia_externa,
                        estado_intento,
                        reembolso_actual_id,
                    ) = fila

                    if (
                        estado_intento != "REEMBOLSO_EN_VERIFICACION"
                        or reembolso_actual_id != reembolso_id
                    ):
                        raise ErrorDeNegocio(
                            409,
                            "ESTADO_INCOMPATIBLE",
                            f"El reembolso no está en REEMBOLSO_EN_VERIFICACION (estado actual: {estado_intento}).",
                        )

                    cur.execute(
                        """
                        UPDATE reembolso
                        SET evidencia_revision = %s,
                            responsable_revision = %s
                        WHERE reembolso_id = %s
                        """,
                        (Jsonb(cuerpo.evidencia), usuario_uuid, reembolso_id),
                    )

                estado_final_intento = _aplicar_resultado_reembolso(
                    conn,
                    reembolso_id=reembolso_id,
                    resultado=cuerpo.resultado,
                )

                if estado_final_intento == "REEMBOLSO_EN_PROCESO":
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT reembolso_actual_id FROM intento_pago WHERE intento_id = %s",
                            (intento_id,),
                        )
                        fila_sig = cur.fetchone()
                        if fila_sig:
                            siguiente_reembolso = fila_sig[0]

                cuerpo_resp = _serializar_reembolso(
                    reembolso_id=reembolso_id,
                    intento_id=intento_id,
                    pedido_id=pedido_id,
                    monto=monto,
                    motivo=motivo,
                    estado_intento=estado_final_intento,
                    referencia_externa=referencia_externa,
                )
                return {
                    "estado_http": 200,
                    "cuerpo": cuerpo_resp,
                    "estado_anterior": {"estado": estado_intento},
                    "estado_resultante": {"estado": estado_final_intento},
                }

            return ejecutar_idempotente(
                conn=conn,
                clave=clave,
                operacion="CL-06",
                objeto="reembolso",
                objeto_id=reembolso_id,
                actor_tipo="USUARIO_OPERATIVO",
                huella=huella,
                trabajo=_trabajo,
                usuario_id=usuario_uuid,
            )

    respuesta_final, es_previo = _ejecutar_con_reintento_concurrencia(_tx_verif_reembolso)

    if siguiente_reembolso is not None and not es_previo:
        enviar_reembolso(siguiente_reembolso)

    response.status_code = respuesta_final["estado_http"]
    if es_previo:
        response.headers["Resultado-Previo"] = "true"
    return respuesta_final["cuerpo"]


# ==============================================================================
# CL-17: Reintentar un reembolso
# ==============================================================================

@router.post("/v1/reembolsos/{reembolso_id}/reintentos")
def reintentar_reembolso(
    reembolso_id: UUID,
    response: Response,
    clave_operacion: str | None = Header(default=None, alias="Clave-Operacion"),
    usuario_id: str | None = Header(default=None, alias="Usuario-Id"),
):
    clave = validar_clave_operacion(clave_operacion)
    usuario_uuid = _parsear_uuid_cabecera(usuario_id, "Usuario-Id")
    huella = calcular_huella("CL-17", reembolso_id, {"usuario_id": str(usuario_uuid)})

    debe_enviar = False

    def _tx_reintentar() -> tuple[dict[str, Any], bool]:
        nonlocal debe_enviar
        with pool.connection() as conn:

            def _trabajo() -> dict[str, Any]:
                nonlocal debe_enviar
                _exigir_rol_responsable(conn, usuario_uuid)

                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT r.reembolso_id, r.intento_id, r.pedido_id, r.monto, r.motivo,
                               r.referencia_externa, i.estado, i.reembolso_actual_id
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
                        pedido_id,
                        monto,
                        motivo,
                        referencia_externa,
                        estado_intento,
                        reembolso_actual_id,
                    ) = fila

                    if estado_intento != "REEMBOLSO_FALLIDO" or reembolso_actual_id != reembolso_id:
                        raise ErrorDeNegocio(
                            409,
                            "ESTADO_INCOMPATIBLE",
                            f"Solo se puede reintentar desde REEMBOLSO_FALLIDO (estado actual: {estado_intento}).",
                        )

                    cur.execute(
                        """
                        UPDATE reembolso
                        SET fecha_envio = NULL,
                            fecha_fallo_confirmado = NULL
                        WHERE reembolso_id = %s
                        """,
                        (reembolso_id,),
                    )
                    cur.execute(
                        """
                        UPDATE intento_pago
                        SET estado = 'REEMBOLSO_EN_PROCESO'
                        WHERE intento_id = %s
                        """,
                        (intento_id,),
                    )
                    debe_enviar = True

                    cuerpo_resp = _serializar_reembolso(
                        reembolso_id=reembolso_id,
                        intento_id=intento_id,
                        pedido_id=pedido_id,
                        monto=monto,
                        motivo=motivo,
                        estado_intento="REEMBOLSO_EN_PROCESO",
                        referencia_externa=referencia_externa,
                    )
                    return {
                        "estado_http": 202,
                        "cuerpo": cuerpo_resp,
                        "estado_anterior": {"estado": "REEMBOLSO_FALLIDO"},
                        "estado_resultante": {"estado": "REEMBOLSO_EN_PROCESO"},
                    }

            return ejecutar_idempotente(
                conn=conn,
                clave=clave,
                operacion="CL-17",
                objeto="reembolso",
                objeto_id=reembolso_id,
                actor_tipo="USUARIO_OPERATIVO",
                huella=huella,
                trabajo=_trabajo,
                usuario_id=usuario_uuid,
            )

    respuesta_final, es_previo = _ejecutar_con_reintento_concurrencia(_tx_reintentar)

    if debe_enviar and not es_previo:
        enviar_reembolso(reembolso_id)

    response.status_code = respuesta_final["estado_http"]
    if es_previo:
        response.headers["Resultado-Previo"] = "true"
    return respuesta_final["cuerpo"]