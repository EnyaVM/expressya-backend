from uuid import UUID

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute


class ErrorDeNegocio(Exception):
    def __init__(self, estado_http: int, codigo: str, mensaje: str, detalle: dict | None = None):
        self.estado_http = estado_http
        self.codigo = codigo
        self.mensaje = mensaje
        self.detalle = detalle or {}

    def cuerpo(self):
        return {"codigo": self.codigo, "mensaje": self.mensaje, "detalle": self.detalle}


class RutaReserva(APIRoute):
    def get_route_handler(self):
        atender = super().get_route_handler()

        async def atender_reserva(peticion: Request):
            try:
                UUID(peticion.headers.get("Cliente-Id", ""))
            except ValueError as error:
                raise ErrorDeNegocio(401, "NO_AUTENTICADO", "Se requiere un Cliente-Id valido") from error
            try:
                return await atender(peticion)
            except RequestValidationError as error:
                campos = [".".join(map(str, fallo["loc"])) for fallo in error.errors()]
                raise ErrorDeNegocio(
                    400, "SOLICITUD_INVALIDA", "Revisa los campos de la solicitud", {"campos": campos}
                ) from error

        return atender_reserva


async def responder_error_de_negocio(peticion: Request, error: ErrorDeNegocio):
    return JSONResponse(
        status_code=error.estado_http,
        content=error.cuerpo(),
    )


async def responder_validacion(peticion: Request, error: RequestValidationError):
    campos = [".".join(map(str, fallo["loc"])) for fallo in error.errors()]
    return JSONResponse(
        status_code=400,
        content=ErrorDeNegocio(400, "SOLICITUD_INVALIDA", "Revisa los campos de la solicitud", {"campos": campos}).cuerpo(),
    )
