from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Header, Response
from pydantic import BaseModel, Field

from app.errores import ErrorDeNegocio
from app.idempotencia import validar_clave_operacion
from app.modulos.faltantes import (FALTANTE, abrir_discrepancia, bloquear_inventario, convertir_en_faltante, exigir_rol,
                                   marcar_faltante, obligaciones, operar, registrar_movimiento, usuario_registrado)

router = APIRouter(tags=["Daños e inspecciones (M01)"])

ACCIONES = {"REINCORPORAR": ("REINCORPORACION", "APTA"), "DESECHAR": ("DESECHO", "NO_APTA")}


class DanoSolicitud(BaseModel):
    cantidad: int = Field(ge=1)
    motivo: str = Field(min_length=1, max_length=200)


class InspeccionSolicitud(BaseModel):
    cantidad: int = Field(ge=1)
    resultado: Literal["APTA", "NO_APTA"]
    evidencia: str = Field(min_length=1, max_length=500)


class ResolucionInspeccionSolicitud(BaseModel):
    accion: Literal["REINCORPORAR", "DESECHAR"]


def cantidad_invalida(mensaje: str) -> ErrorDeNegocio:
    return ErrorDeNegocio(422, "CANTIDAD_INVALIDA", mensaje)


def movimiento(movimiento_id, tipo, saldos: dict, **extra) -> dict:
    return {"movimiento_id": str(movimiento_id), "tipo": tipo,
            "saldos": [{"saldo": s, "cantidad": c} for s, c in saldos.items()], **extra}


def abrir_por_deficit(conn, tienda, producto, movimiento_id, clave) -> UUID:
    discrepancia_id = convertir_en_faltante(conn, tienda, producto) or abrir_discrepancia(
        conn, tienda, producto, FALTANTE, "M01", movimiento_dano_id=movimiento_id)
    marcar_faltante(conn, tienda, producto, discrepancia_id, clave)
    return discrepancia_id


def reportar_dano(conn, tienda, producto, usuario_id, solicitud: DanoSolicitud, clave) -> dict:
    exigir_rol(conn, usuario_id, tienda, ("PERSONAL_TIENDA",))
    e, _ = bloquear_inventario(conn, tienda, producto)
    if solicitud.cantidad > e:
        raise cantidad_invalida("No se pueden dañar más unidades de las que hay en existencias.")
    conn.execute("UPDATE inventario SET e = e - %s, n = n + %s WHERE darkstore_id = %s AND producto_id = %s",
                 (solicitud.cantidad, solicitud.cantidad, tienda, producto))
    saldos = {"E": -solicitud.cantidad, "N": solicitud.cantidad}
    movimiento_id = registrar_movimiento(conn, tienda, producto, "DANO", solicitud.motivo, usuario_id, clave, saldos)
    deficit = e - solicitud.cantidad < obligaciones(conn, tienda, producto)
    discrepancia_id = abrir_por_deficit(conn, tienda, producto, movimiento_id, clave) if deficit else None
    return movimiento(movimiento_id, "DANO", saldos, discrepancia_id=str(discrepancia_id) if discrepancia_id else None)


def leer_dano(conn, movimiento_id) -> tuple[UUID, UUID, int]:
    fila = conn.execute(
        "SELECT m.darkstore_id, m.producto_id, s.cantidad FROM movimiento_inventario m "
        "JOIN movimiento_saldo s ON s.movimiento_id = m.movimiento_id AND s.saldo = 'N' "
        "WHERE m.movimiento_id = %s AND m.tipo = 'DANO'", (movimiento_id,),
    ).fetchone()
    if fila is None:
        raise ErrorDeNegocio(404, "NO_ENCONTRADO", "No existe un daño con ese movimiento.")
    return fila


def inspeccionar(conn, movimiento_id, usuario_id, solicitud: InspeccionSolicitud) -> dict:
    tienda, producto, danadas = leer_dano(conn, movimiento_id)
    exigir_rol(conn, usuario_id, tienda, ("INSPECTOR",))
    bloquear_inventario(conn, tienda, producto)
    inspeccionadas = conn.execute("SELECT COALESCE(SUM(cantidad), 0) FROM inspeccion_dano WHERE movimiento_dano_id = %s",
                                  (movimiento_id,)).fetchone()[0]
    if inspeccionadas + solicitud.cantidad > danadas:
        raise cantidad_invalida(f"Solo quedan {danadas - inspeccionadas} unidades de este daño por inspeccionar.")
    inspeccion_id = conn.execute(
        "INSERT INTO inspeccion_dano (movimiento_dano_id, darkstore_id, producto_id, cantidad, resultado, evidencia, inspeccionado_por) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING inspeccion_id",
        (movimiento_id, tienda, producto, solicitud.cantidad, solicitud.resultado, solicitud.evidencia, usuario_id),
    ).fetchone()[0]
    return {"inspeccion_id": str(inspeccion_id), "resultado": solicitud.resultado, "cantidad": solicitud.cantidad,
            "inspeccionado_por": str(usuario_id)}


def leer_inspeccion(conn, inspeccion_id) -> tuple:
    fila = conn.execute("SELECT darkstore_id, producto_id, cantidad, resultado, inspeccionado_por FROM inspeccion_dano "
                        "WHERE inspeccion_id = %s", (inspeccion_id,)).fetchone()
    if fila is None:
        raise ErrorDeNegocio(404, "NO_ENCONTRADO", "La inspección no existe.")
    return fila


def autorizacion_invalida(resultado, esperado, inspector, usuario_id) -> str | None:
    if resultado != esperado:
        return f"Una inspección {resultado} no permite esta acción."
    if inspector == usuario_id:
        return "Quien inspeccionó no puede autorizar su propia inspección (D-09)."
    return None


def resolver_inspeccion(conn, inspeccion_id, usuario_id, accion: str, clave) -> dict:
    tienda, producto, cantidad, resultado, inspector = leer_inspeccion(conn, inspeccion_id)
    exigir_rol(conn, usuario_id, tienda, ("AUTORIZADOR",))
    bloquear_inventario(conn, tienda, producto)
    if conn.execute("SELECT 1 FROM movimiento_inventario WHERE referencia_inspeccion_id = %s", (inspeccion_id,)).fetchone():
        raise ErrorDeNegocio(409, "ESTADO_INCOMPATIBLE", "Esta inspección ya fue resuelta.")
    tipo, esperado = ACCIONES[accion]
    motivo = autorizacion_invalida(resultado, esperado, inspector, usuario_id)
    if motivo:
        raise ErrorDeNegocio(422, "AUTORIZACION_INVALIDA", motivo)
    saldos = {"N": -cantidad, "E": cantidad} if tipo == "REINCORPORACION" else {"N": -cantidad}
    conn.execute("UPDATE inventario SET e = e + %s, n = n - %s WHERE darkstore_id = %s AND producto_id = %s",
                 (saldos.get("E", 0), cantidad, tienda, producto))
    movimiento_id = registrar_movimiento(conn, tienda, producto, tipo, f"Inspección {resultado}", usuario_id, clave, saldos,
                                         autorizado_por=usuario_id, inspeccion=inspeccion_id)
    return movimiento(movimiento_id, tipo, saldos)


@router.post("/v1/darkstores/{darkstore_id}/productos/{producto_id}/danos", status_code=201)
def reportar_dano_http(darkstore_id: UUID, producto_id: UUID, cuerpo: DanoSolicitud, response: Response,
                       clave_operacion: str | None = Header(default=None, alias="Clave-Operacion"),
                       usuario_id: str | None = Header(default=None, alias="Usuario-Id")):
    clave = validar_clave_operacion(clave_operacion)
    usuario = usuario_registrado(usuario_id)
    return operar("CL-14", "producto", producto_id, usuario, clave, {"darkstore_id": str(darkstore_id), **cuerpo.model_dump()},
                  lambda conn: reportar_dano(conn, darkstore_id, producto_id, usuario, cuerpo, clave), response, 201)


@router.post("/v1/danos/{movimiento_id}/inspecciones", status_code=201)
def inspeccionar_http(movimiento_id: UUID, cuerpo: InspeccionSolicitud, response: Response,
                      clave_operacion: str | None = Header(default=None, alias="Clave-Operacion"),
                      usuario_id: str | None = Header(default=None, alias="Usuario-Id")):
    clave = validar_clave_operacion(clave_operacion)
    usuario = usuario_registrado(usuario_id)
    return operar("CL-15", "movimiento", movimiento_id, usuario, clave, cuerpo.model_dump(),
                  lambda conn: inspeccionar(conn, movimiento_id, usuario, cuerpo), response, 201)


@router.post("/v1/inspecciones/{inspeccion_id}/resolucion", status_code=201)
def resolver_inspeccion_http(inspeccion_id: UUID, cuerpo: ResolucionInspeccionSolicitud, response: Response,
                             clave_operacion: str | None = Header(default=None, alias="Clave-Operacion"),
                             usuario_id: str | None = Header(default=None, alias="Usuario-Id")):
    clave = validar_clave_operacion(clave_operacion)
    usuario = usuario_registrado(usuario_id)
    return operar("CL-15", "inspeccion", inspeccion_id, usuario, clave, cuerpo.model_dump(),
                  lambda conn: resolver_inspeccion(conn, inspeccion_id, usuario, cuerpo.accion, clave), response, 201)
