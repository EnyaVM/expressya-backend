import base64
import hashlib
import hmac
import os
import re
import secrets
from datetime import timedelta

import anyio
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from app.db import pool
from app.errores import ErrorDeNegocio

PORTALES = ("cliente", "interno")
PREFIJO = "expressya_sesion_"
VENTANA = re.compile(r"[A-Za-z0-9-]{1,64}")
VENTANA_INVALIDA = ErrorDeNegocio(400, "SOLICITUD_INVALIDA", "El identificador de la pestaña (X-Ventana) no es válido.")
DURACION = timedelta(hours=8)
LECTURA = {"GET", "HEAD", "OPTIONS"}
SIN_CSRF = ("/v1/auth/login", "/v1/auth/registro")
MUTABLES_PARA_ADMINISTRACION = ("/v1/auth/", "/v1/admin/", "/simulador/", "/v1/pasarela/notificaciones")
IDENTIDADES = (b"cliente-id", b"usuario-id")
SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def cifrar_clave(clave: str) -> str:
    sal = secrets.token_bytes(16)
    derivada = hashlib.scrypt(clave.encode(), salt=sal, **SCRYPT)
    return "scrypt$" + base64.b64encode(sal).decode() + "$" + base64.b64encode(derivada).decode()


def clave_correcta(clave: str, guardada: str) -> bool:
    _, sal, derivada = guardada.split("$")
    calculada = hashlib.scrypt(clave.encode(), salt=base64.b64decode(sal), **SCRYPT)
    return hmac.compare_digest(calculada, base64.b64decode(derivada))


def huella(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def abrir_sesion(conn, cuenta_id, portal: str) -> tuple[str, str]:
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO sesion (token_hash, cuenta_id, portal, csrf, expira_en) "
        "VALUES (%s, %s, %s, %s, clock_timestamp() + %s)",
        (huella(token), cuenta_id, portal, csrf, DURACION),
    )
    return token, csrf


def cerrar_sesion(conn, token: str) -> None:
    conn.execute("UPDATE sesion SET cerrada_en = clock_timestamp() WHERE token_hash = %s AND cerrada_en IS NULL",
                 (huella(token),))


def leer_sesion(token: str) -> dict | None:
    with pool.connection() as conn:
        fila = conn.execute(
            "SELECT c.cuenta_id, c.tipo, c.cliente_id, c.usuario_id, s.csrf, s.portal "
            "FROM sesion s JOIN cuenta c USING (cuenta_id) "
            "WHERE s.token_hash = %s AND s.cerrada_en IS NULL AND s.expira_en > clock_timestamp() AND c.activo",
            (huella(token),),
        ).fetchone()
    if fila is None:
        return None
    return dict(zip(("cuenta_id", "tipo", "cliente_id", "usuario_id", "csrf", "portal"), fila))


def encabezados_de_prueba() -> bool:
    return os.getenv("EXPRESSYA_ENCABEZADOS_DE_PRUEBA") == "1"


def ventana_de(cabeceras: Headers) -> str | None:
    ventana = cabeceras.get("x-ventana")
    if ventana is not None and not VENTANA.fullmatch(ventana):
        raise VENTANA_INVALIDA
    return ventana


def galleta(portal: str, ventana: str | None = None) -> str:
    return PREFIJO + portal + (f"_{ventana}" if ventana else "")


def token_de(cabeceras: Headers) -> tuple[str | None, str | None]:
    autorizacion = cabeceras.get("authorization", "")
    if autorizacion.lower().startswith("bearer "):
        return autorizacion[7:].strip(), "bearer"
    galletas = dict(p.strip().split("=", 1) for p in cabeceras.get("cookie", "").split(";") if "=" in p)
    portal = cabeceras.get("x-portal")
    if portal is not None:
        nombre = galleta(portal, ventana_de(cabeceras)) if portal in PORTALES else None
        return (galletas[nombre], "cookie") if nombre in galletas else (None, None)
    nuestras = [valor for nombre, valor in galletas.items() if nombre.startswith(PREFIJO)]
    return (nuestras[0], "cookie") if len(nuestras) == 1 else (None, None)


def rechazo(metodo: str, ruta: str, sesion: dict | None, via: str | None, cabeceras: Headers) -> ErrorDeNegocio | None:
    if sesion is None or metodo in LECTURA:
        return None
    if via == "cookie" and ruta not in SIN_CSRF and not hmac.compare_digest(cabeceras.get("x-csrf-token", ""), sesion["csrf"]):
        return ErrorDeNegocio(403, "SIN_PERMISO", "Falta o no coincide el token CSRF de la sesión.")
    if sesion["tipo"] == "ADMINISTRADOR" and not ruta.startswith(MUTABLES_PARA_ADMINISTRACION):
        return ErrorDeNegocio(403, "SIN_PERMISO", "La cuenta de administración solo consulta; no opera en nombre de otras personas.")
    return None


def con_identidad(crudas: list, sesion: dict | None) -> list:
    if sesion is None and encabezados_de_prueba():
        return crudas
    limpias = [(k, v) for k, v in crudas if k.lower() not in IDENTIDADES]
    if sesion and sesion["cliente_id"]:
        limpias.append((b"cliente-id", str(sesion["cliente_id"]).encode()))
    if sesion and sesion["usuario_id"]:
        limpias.append((b"usuario-id", str(sesion["usuario_id"]).encode()))
    return limpias


class Autenticacion:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        cabeceras = Headers(scope=scope)
        try:
            ventana_de(cabeceras)
            token, via = token_de(cabeceras)
        except ErrorDeNegocio as invalida:
            await JSONResponse(status_code=invalida.estado_http, content=invalida.cuerpo())(scope, receive, send)
            return
        sesion = await anyio.to_thread.run_sync(leer_sesion, token) if token else None
        error = rechazo(scope["method"], scope["path"], sesion, via, cabeceras)
        if error:
            await JSONResponse(status_code=error.estado_http, content=error.cuerpo())(scope, receive, send)
            return
        scope["headers"] = con_identidad(list(scope["headers"]), sesion)
        scope.setdefault("state", {})["sesion"] = sesion
        scope["state"]["token"] = token
        await self.app(scope, receive, send)


def sesion_de(request) -> dict | None:
    return request.scope.get("state", {}).get("sesion")


def exigir_sesion(request) -> dict:
    sesion = sesion_de(request)
    if sesion is None:
        raise ErrorDeNegocio(401, "NO_AUTENTICADO", "Inicia sesión para continuar.")
    return sesion


def es_administracion(request) -> bool:
    sesion = sesion_de(request)
    return bool(sesion and sesion["tipo"] == "ADMINISTRADOR")
