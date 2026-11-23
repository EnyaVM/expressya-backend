from typing import Literal
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel

from app.errores import ErrorDeNegocio

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


def cobrar(intento_id: UUID, monto: str) -> tuple[str, str] | None:
    """
    Simula un cobro síncrono contra la pasarela externa.
    Devuelve ('APROBADO'|'RECHAZADO', referencia_externa) o None si simula timeout (30s).
    """
    modo = _estado_simulador["cobro"]
    if modo == "NO_RESPONDER":
        return None
    referencia = _siguiente_referencia()
    if modo == "APROBAR":
        return ("APROBADO", referencia)
    return ("RECHAZADO", referencia)


def reembolsar(reembolso_id: UUID, monto: str) -> tuple[str, str] | None:
    """
    Simula un reembolso contra la pasarela externa.
    Devuelve ('CONFIRMADO'|'FALLIDO', referencia_externa) o None si simula timeout.
    """
    modo = _estado_simulador["reembolso"]
    if modo == "NO_RESPONDER":
        return None
    referencia = _siguiente_referencia()
    if modo == "CONFIRMAR":
        return ("CONFIRMADO", referencia)
    return ("FALLIDO", referencia)


class ConfiguracionModoSolicitud(BaseModel):
    cobro: str | None = None
    reembolso: str | None = None


@router.get("/modo")
def obtener_modo() -> dict[str, str]:
    """Consulta el modo actual configurado en la pasarela simulada."""
    return dict(_estado_simulador)


@router.post("/modo")
def configurar_modo(cuerpo: ConfiguracionModoSolicitud) -> dict[str, str]:
    """Cambia dinámicamente el comportamiento de cobro y/o reembolso del simulador."""
    if cuerpo.cobro is not None:
        if cuerpo.cobro not in ("APROBAR", "RECHAZAR", "NO_RESPONDER"):
            raise ErrorDeNegocio(
                400,
                "SOLICITUD_INVALIDA",
                f"Modo de cobro inválido: {cuerpo.cobro}",
            )
        _estado_simulador["cobro"] = cuerpo.cobro

    if cuerpo.reembolso is not None:
        if cuerpo.reembolso not in ("CONFIRMAR", "FALLAR", "NO_RESPONDER"):
            raise ErrorDeNegocio(
                400,
                "SOLICITUD_INVALIDA",
                f"Modo de reembolso inválido: {cuerpo.reembolso}",
            )
        _estado_simulador["reembolso"] = cuerpo.reembolso

    return dict(_estado_simulador)