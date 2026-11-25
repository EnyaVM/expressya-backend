from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.errores import ErrorDeNegocio
from app.seguridad import encabezados_de_prueba, es_administracion

router = APIRouter(prefix="/simulador", tags=["Simulador Pasarela (No CL)"])

ModoCobro = Literal["APROBAR", "RECHAZAR", "NO_RESPONDER"]
ModoReembolso = Literal["CONFIRMAR", "FALLAR", "NO_RESPONDER"]

_estado_simulador: dict[str, str] = {
    "cobro": "APROBAR",
    "reembolso": "CONFIRMAR",
}
_contador_referencia: int = 100


def reiniciar_simulador() -> None:
    """Restaura los valores por defecto del simulador (útil para fixtures de prueba)."""
    global _contador_referencia
    _estado_simulador["cobro"] = "APROBAR"
    _estado_simulador["reembolso"] = "CONFIRMAR"
    _contador_referencia = 100


def _siguiente_referencia() -> str:
    global _contador_referencia
    _contador_referencia += 1
    return f"PSP-{_contador_referencia:06d}"


def responder(modo: str, exito: str, fallo: str) -> tuple[str, str] | None:
    if modo == "NO_RESPONDER":
        return None
    return (exito if modo in ("APROBAR", "CONFIRMAR") else fallo), _siguiente_referencia()


def cobrar(intento_id: UUID, monto: str) -> tuple[str, str] | None:
    return responder(_estado_simulador["cobro"], "APROBADO", "RECHAZADO")


def reembolsar(reembolso_id: UUID, monto: str) -> tuple[str, str] | None:
    return responder(_estado_simulador["reembolso"], "CONFIRMADO", "FALLIDO")


class ConfiguracionModoSolicitud(BaseModel):
    cobro: ModoCobro | None = None
    reembolso: ModoReembolso | None = None


@router.get("/modo")
def obtener_modo() -> dict[str, str]:
    return dict(_estado_simulador)


@router.post("/modo")
def configurar_modo(cuerpo: ConfiguracionModoSolicitud, request: Request) -> dict[str, str]:
    if not (encabezados_de_prueba() or es_administracion(request)):
        raise ErrorDeNegocio(403, "SIN_PERMISO", "Solo administración configura la pasarela de prueba.")
    _estado_simulador.update(cuerpo.model_dump(exclude_none=True))
    return dict(_estado_simulador)
