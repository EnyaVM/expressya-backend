import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.staticfiles import StaticFiles

from app.db import pool
from fastapi.exceptions import RequestValidationError

from app.errores import ErrorDeNegocio, responder_error_de_negocio, responder_validacion
from app.modulos import cuentas, portal
from app.seguridad import Autenticacion
from app.modulos import cancelaciones, danos, faltantes, inventario, pagos, pasarela_simulada, pedidos, reservas

SEGUNDOS_ENTRE_REVISIONES = 5

registro = logging.getLogger("expressya.vencimiento")
registro_pagos = logging.getLogger("expressya.pagos")


def expirar_reservas():
    expiradas = reservas.expirar_vencidas()
    if expiradas:
        registro.info("Reservas expiradas: %s", expiradas)


def procesar_pagos():
    for nombre, tarea in (("Eventos EV-01 procesados", pedidos.procesar_eventos_pendientes),
                          ("Intentos INICIADO recuperados a EN_VERIFICACION", pagos.recuperar_intentos_iniciados),
                          ("Reembolsos sin enviar recuperados", pagos.recuperar_reembolsos_sin_enviar)):
        cantidad = tarea()
        if cantidad:
            registro_pagos.info("%s: %s", nombre, cantidad)


def ejecutar_ciclo(tarea, bitacora) -> bool:
    try:
        tarea()
        return True
    except Exception:
        bitacora.exception("Fallo el ciclo %s", tarea.__name__)
        return False


async def repetir(tarea, bitacora):
    while True:
        await asyncio.to_thread(ejecutar_ciclo, tarea, bitacora)
        await asyncio.sleep(SEGUNDOS_ENTRE_REVISIONES)


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    tarea_vencimiento = asyncio.create_task(repetir(expirar_reservas, registro))
    tarea_eventos = asyncio.create_task(repetir(procesar_pagos, registro_pagos))
    yield
    tarea_vencimiento.cancel()
    tarea_eventos.cancel()


app = FastAPI(title="ExpressYa", lifespan=ciclo_de_vida)
app.add_exception_handler(ErrorDeNegocio, responder_error_de_negocio)
app.add_exception_handler(RequestValidationError, responder_validacion)
app.add_middleware(Autenticacion)
app.include_router(cuentas.router)
app.include_router(portal.router)
app.include_router(inventario.router)
app.include_router(reservas.router)
app.include_router(pedidos.router)
app.include_router(pagos.router)
app.include_router(pasarela_simulada.router)
app.include_router(cancelaciones.router)
app.include_router(faltantes.router)
app.include_router(danos.router)


def esquema_con_sesion():
    if app.openapi_schema is None:
        esquema = get_openapi(title=app.title, version="1.1.0", routes=app.routes)
        esquema["components"]["securitySchemes"] = {"sesion": {"type": "http", "scheme": "bearer"}}
        esquema["security"] = [{"sesion": []}]
        app.openapi_schema = esquema
    return app.openapi_schema


app.openapi = esquema_con_sesion


@app.get("/v1/salud")
def salud():
    with pool.connection() as conn:
        hora = conn.execute("SELECT clock_timestamp()").fetchone()[0]
    return {"estado": "ok", "hora_servidor": hora}


app.mount("/", StaticFiles(directory="static", html=True), name="static")
