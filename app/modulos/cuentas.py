from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Request, Response
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from app.db import pool
from app.errores import ErrorDeNegocio
from app.seguridad import (COOKIE, DURACION, abrir_sesion, cerrar_sesion, cifrar_clave, clave_correcta,
                           exigir_sesion)

router = APIRouter(tags=["Cuentas y sesiones"])

ROLES = ("PICKER", "PERSONAL_TIENDA", "SUPERVISOR", "INSPECTOR", "AUTORIZADOR", "RESPONSABLE")
PORTAL_DE = {"CLIENTE": "cliente", "INTERNO": "interno", "ADMINISTRADOR": "interno"}
CREDENCIALES_INVALIDAS = ErrorDeNegocio(401, "NO_AUTENTICADO", "Usuario o contraseña incorrectos.")
CLAVE_FICTICIA = cifrar_clave("sin-cuenta-con-este-usuario")
Usuario = Field(pattern=r"^[a-z0-9._-]{3,40}$")
Clave = Field(min_length=8, max_length=128)
Nombre = Field(min_length=1, max_length=120)


class Asignacion(BaseModel):
    darkstore_id: UUID
    rol: Literal[ROLES]


class RegistroSolicitud(BaseModel):
    usuario: str = Usuario
    nombre: str = Nombre
    clave: str = Clave


class AltaSolicitud(RegistroSolicitud):
    tipo: Literal["CLIENTE", "INTERNO", "ADMINISTRADOR"]
    asignaciones: list[Asignacion] = []


class LoginSolicitud(BaseModel):
    usuario: str = Field(min_length=1, max_length=40)
    clave: str = Field(min_length=1, max_length=128)
    portal: Literal["cliente", "interno"]
    entregar_token: bool = False


class AccesoSolicitud(BaseModel):
    activo: bool


def filas(conn, sql, args=()):
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(sql, args).fetchall()


def identidad_para(conn, tipo: str, nombre: str) -> tuple[UUID | None, UUID | None]:
    if tipo == "CLIENTE":
        return conn.execute("INSERT INTO cliente DEFAULT VALUES RETURNING cliente_id").fetchone()[0], None
    if tipo == "INTERNO":
        return None, conn.execute("INSERT INTO usuario_operativo (nombre) VALUES (%s) RETURNING usuario_id", (nombre,)).fetchone()[0]
    return None, None


def crear_cuenta(conn, usuario, nombre, clave, tipo, asignaciones=(), actor=None) -> UUID:
    if (tipo == "INTERNO") != bool(asignaciones):
        raise ErrorDeNegocio(400, "SOLICITUD_INVALIDA", "Solo el personal interno lleva perfiles, y necesita al menos uno.")
    with conn.transaction():
        cliente_id, usuario_id = identidad_para(conn, tipo, nombre)
        try:
            with conn.transaction():
                cuenta_id = conn.execute(
                    "INSERT INTO cuenta (usuario, nombre, clave_hash, tipo, cliente_id, usuario_id) "
                    "VALUES (%s, %s, %s, %s, %s, %s) RETURNING cuenta_id",
                    (usuario, nombre, cifrar_clave(clave), tipo, cliente_id, usuario_id),
                ).fetchone()[0]
        except psycopg.errors.UniqueViolation as error:
            raise ErrorDeNegocio(409, "ESTADO_INCOMPATIBLE", "Ese usuario ya existe.") from error
        for tienda, rol in asignaciones:
            conn.execute("INSERT INTO asignacion_rol (usuario_id, darkstore_id, rol) VALUES (%s, %s, %s)", (usuario_id, tienda, rol))
        detalle = {"tipo": tipo, "asignaciones": [{"darkstore_id": str(t), "rol": r} for t, r in asignaciones]}
        conn.execute("INSERT INTO auditoria_cuenta (accion, cuenta_id, actor_cuenta_id, detalle) VALUES ('ALTA_CUENTA', %s, %s, %s)",
                     (cuenta_id, actor, Jsonb(detalle)))
    return cuenta_id


def describir(conn, cuenta_id) -> dict:
    cuenta = filas(conn, "SELECT cuenta_id, usuario, nombre, tipo, activo, creado_en, usuario_id FROM cuenta WHERE cuenta_id = %s",
                   (cuenta_id,))[0]
    cuenta["asignaciones"] = filas(
        conn,
        "SELECT a.darkstore_id, a.rol, d.nombre AS tienda FROM asignacion_rol a JOIN dark_store d USING (darkstore_id) "
        "WHERE a.usuario_id = %s ORDER BY d.nombre, a.rol",
        (cuenta.pop("usuario_id"),),
    )
    return cuenta


def exigir_administracion(request: Request) -> dict:
    sesion = exigir_sesion(request)
    if sesion["tipo"] != "ADMINISTRADOR":
        raise ErrorDeNegocio(403, "SIN_PERMISO", "Solo administración gestiona cuentas.")
    return sesion


@router.post("/v1/auth/registro", status_code=201)
def registrar(solicitud: RegistroSolicitud):
    with pool.connection() as conn:
        cuenta_id = crear_cuenta(conn, solicitud.usuario, solicitud.nombre, solicitud.clave, "CLIENTE")
        return describir(conn, cuenta_id)


@router.post("/v1/auth/login")
def entrar(solicitud: LoginSolicitud, response: Response):
    with pool.connection() as conn:
        fila = conn.execute("SELECT cuenta_id, clave_hash, tipo FROM cuenta WHERE usuario = %s AND activo",
                            (solicitud.usuario.strip().lower(),)).fetchone()
        correcta = clave_correcta(solicitud.clave, fila[1] if fila else CLAVE_FICTICIA)
        if fila is None or not correcta or PORTAL_DE[fila[2]] != solicitud.portal:
            raise CREDENCIALES_INVALIDAS
        token, csrf = abrir_sesion(conn, fila[0], solicitud.portal)
        cuenta = describir(conn, fila[0]) | {"csrf": csrf}
    if solicitud.entregar_token:
        return cuenta | {"token": token}
    response.set_cookie(COOKIE, token, max_age=int(DURACION.total_seconds()), httponly=True, samesite="strict", path="/")
    return cuenta


@router.post("/v1/auth/logout")
def salir(request: Request, response: Response):
    exigir_sesion(request)
    with pool.connection() as conn:
        cerrar_sesion(conn, request.scope["state"]["token"])
    response.delete_cookie(COOKIE, path="/")
    return {"estado": "cerrada"}


@router.get("/v1/auth/me")
def yo(request: Request):
    sesion = exigir_sesion(request)
    with pool.connection() as conn:
        return describir(conn, sesion["cuenta_id"]) | {"csrf": sesion["csrf"]}


@router.get("/v1/admin/cuentas")
def cuentas(request: Request):
    exigir_administracion(request)
    with pool.connection() as conn:
        ids = [f[0] for f in conn.execute("SELECT cuenta_id FROM cuenta ORDER BY creado_en DESC")]
        return [describir(conn, cuenta_id) for cuenta_id in ids]


@router.post("/v1/admin/cuentas", status_code=201)
def dar_de_alta(solicitud: AltaSolicitud, request: Request):
    sesion = exigir_administracion(request)
    asignaciones = sorted({(a.darkstore_id, a.rol) for a in solicitud.asignaciones})
    with pool.connection() as conn:
        cuenta_id = crear_cuenta(conn, solicitud.usuario, solicitud.nombre, solicitud.clave, solicitud.tipo,
                                 asignaciones, sesion["cuenta_id"])
        return describir(conn, cuenta_id)


@router.patch("/v1/admin/cuentas/{cuenta_id}")
def cambiar_acceso(cuenta_id: UUID, solicitud: AccesoSolicitud, request: Request):
    sesion = exigir_administracion(request)
    if cuenta_id == sesion["cuenta_id"]:
        raise ErrorDeNegocio(409, "ESTADO_INCOMPATIBLE", "No puedes cambiar el acceso de tu propia cuenta.")
    with pool.connection() as conn, conn.transaction():
        if conn.execute("UPDATE cuenta SET activo = %s WHERE cuenta_id = %s RETURNING cuenta_id",
                        (solicitud.activo, cuenta_id)).fetchone() is None:
            raise ErrorDeNegocio(404, "NO_ENCONTRADO", "La cuenta no existe.")
        if not solicitud.activo:
            conn.execute("UPDATE sesion SET cerrada_en = clock_timestamp() WHERE cuenta_id = %s AND cerrada_en IS NULL", (cuenta_id,))
        conn.execute("INSERT INTO auditoria_cuenta (accion, cuenta_id, actor_cuenta_id, detalle) VALUES ('CAMBIO_ACCESO', %s, %s, %s)",
                     (cuenta_id, sesion["cuenta_id"], Jsonb({"activo": solicitud.activo})))
        return describir(conn, cuenta_id)


@router.get("/v1/admin/auditoria")
def auditoria(request: Request):
    exigir_administracion(request)
    with pool.connection() as conn:
        return filas(conn, "SELECT a.accion, c.usuario AS cuenta, actor.usuario AS actor, a.detalle, a.fecha "
                           "FROM auditoria_cuenta a JOIN cuenta c USING (cuenta_id) "
                           "LEFT JOIN cuenta actor ON actor.cuenta_id = a.actor_cuenta_id "
                           "ORDER BY a.fecha DESC LIMIT 200")
