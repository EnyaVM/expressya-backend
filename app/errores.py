from fastapi import Request
from fastapi.responses import JSONResponse


class ErrorDeNegocio(Exception):
    def __init__(self, estado_http: int, codigo: str, mensaje: str, detalle: dict | None = None):
        self.estado_http = estado_http
        self.codigo = codigo
        self.mensaje = mensaje
        self.detalle = detalle or {}


async def responder_error_de_negocio(peticion: Request, error: ErrorDeNegocio):
    return JSONResponse(
        status_code=error.estado_http,
        content={"codigo": error.codigo, "mensaje": error.mensaje, "detalle": error.detalle},
    )