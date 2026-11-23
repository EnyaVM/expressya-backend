import hashlib
import json
from typing import Any, Callable
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from app.db import pool
from app.errores import ErrorDeNegocio


def validar_clave_operacion(clave_str: str | None) -> UUID:
    """Valida que la cabecera Clave-Operacion exista y sea un UUID válido."""
    if not clave_str:
        raise ErrorDeNegocio(
            400,
            "SOLICITUD_INVALIDA",
            "Falta la cabecera obligatoria Clave-Operacion.",
        )
    try:
        return UUID(str(clave_str).strip())
    except ValueError as exc:
        raise ErrorDeNegocio(
            400,
            "SOLICITUD_INVALIDA",
            "La cabecera Clave-Operacion debe ser un UUID válido.",
        ) from exc


def clave_tomada_por_un_pago(conn, clave: UUID) -> bool:
    return conn.execute(
        "SELECT 1 FROM intento_pago WHERE clave_operacion = %s",
        (clave,),
    ).fetchone() is not None


def calcular_huella(operacion: str, objeto_id: Any, cuerpo: dict[str, Any]) -> bytes:
    """Genera el hash SHA-256 (32 bytes) del JSON canónico de la solicitud."""
    canonico = json.dumps(
        {
            "operacion": operacion,
            "objeto_id": str(objeto_id),
            "cuerpo": cuerpo,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonico.encode("utf-8")).digest()


def _guardar_rechazo_aparte(
    clave: UUID,
    operacion: str,
    objeto: str,
    objeto_id: UUID,
    huella: bytes,
    actor_tipo: str,
    usuario_id: UUID | None,
    error: ErrorDeNegocio,
) -> None:
    """Guarda un registro RECHAZADA en una transacción independiente tras un rollback."""
    respuesta_error = {
        "estado_http": error.estado_http,
        "cuerpo": {
            "codigo": error.codigo,
            "mensaje": error.mensaje,
            "detalle": error.detalle,
        },
    }
    with pool.connection() as conn_rej:
        with conn_rej.transaction():
            with conn_rej.cursor() as cur:
                cur.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s::text, 0))",
                    (str(clave),),
                )
                cur.execute(
                    """
                    INSERT INTO registro_operacion (
                        registro_id, clave_operacion, operacion, objeto, objeto_id,
                        huella_solicitud, estado_anterior, estado_resultante,
                        actor_tipo, usuario_id, fecha, resultado, respuesta, motivo_rechazo
                    )
                    VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, clock_timestamp(), 'RECHAZADA', %s, %s
                    )
                    ON CONFLICT (clave_operacion) DO NOTHING
                    """,
                    (
                        uuid4(),
                        clave,
                        operacion,
                        objeto,
                        objeto_id,
                        huella,
                        Jsonb({}),
                        Jsonb({}),
                        actor_tipo,
                        usuario_id,
                        Jsonb(respuesta_error),
                        error.codigo,
                    ),
                )


def ejecutar_idempotente(
    conn,
    clave: UUID,
    operacion: str,
    objeto: str,
    objeto_id: UUID,
    actor_tipo: str,
    huella: bytes,
    trabajo: Callable[[], dict[str, Any]],
    usuario_id: UUID | None = None,
) -> tuple[dict[str, Any], bool]:
    """
    Ejecuta una operación de forma idempotente dentro de la conexión dada.
    Devuelve (respuesta_dict, es_resultado_previo).
    El dict de respuesta tiene la forma {"estado_http": int, "cuerpo": dict}.
    """
    try:
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
                fila = cur.fetchone()

                if fila is not None:
                    huella_guardada, resultado_guardado, respuesta_guardada = fila
                    if bytes(huella_guardada) != huella:
                        raise ErrorDeNegocio(
                            409,
                            "CONFLICTO_DE_CLAVE",
                            "La Clave-Operacion ya fue utilizada con parámetros distintos.",
                        )
                    if resultado_guardado == "RECHAZADA":
                        cuerpo_err = respuesta_guardada.get("cuerpo", {})
                        raise ErrorDeNegocio(
                            respuesta_guardada.get("estado_http", 400),
                            cuerpo_err.get("codigo", "SOLICITUD_INVALIDA"),
                            cuerpo_err.get("mensaje", "Operación previamente rechazada."),
                            cuerpo_err.get("detalle"),
                        )
                    return respuesta_guardada, True

                if operacion != "CL-04" and clave_tomada_por_un_pago(conn, clave):
                    raise ErrorDeNegocio(
                        409,
                        "CONFLICTO_DE_CLAVE",
                        "La Clave-Operacion ya fue utilizada por un pago.",
                    )

                resultado_trabajo = trabajo()
                respuesta_final = {
                    "estado_http": resultado_trabajo["estado_http"],
                    "cuerpo": resultado_trabajo["cuerpo"],
                }
                estado_anterior = resultado_trabajo.get("estado_anterior", {})
                estado_resultante = resultado_trabajo.get("estado_resultante", {})

                cur.execute(
                    """
                    INSERT INTO registro_operacion (
                        registro_id, clave_operacion, operacion, objeto, objeto_id,
                        huella_solicitud, estado_anterior, estado_resultante,
                        actor_tipo, usuario_id, fecha, resultado, respuesta, motivo_rechazo
                    )
                    VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, clock_timestamp(), 'APLICADA', %s, NULL
                    )
                    """,
                    (
                        uuid4(),
                        clave,
                        operacion,
                        objeto,
                        objeto_id,
                        huella,
                        Jsonb(estado_anterior),
                        Jsonb(estado_resultante),
                        actor_tipo,
                        usuario_id,
                        Jsonb(respuesta_final),
                    ),
                )
                return respuesta_final, False

    except ErrorDeNegocio as err:
        # Si fue un conflicto de clave o una repetición de rechazo, no sobrescribimos
        if err.codigo != "CONFLICTO_DE_CLAVE":
            _guardar_rechazo_aparte(
                clave=clave,
                operacion=operacion,
                objeto=objeto,
                objeto_id=objeto_id,
                huella=huella,
                actor_tipo=actor_tipo,
                usuario_id=usuario_id,
                error=err,
            )
        raise