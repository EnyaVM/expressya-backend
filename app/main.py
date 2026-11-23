import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.db import pool
from app.errores import ErrorDeNegocio, responder_error_de_negocio
from app.modulos import inventario, pagos, pasarela_simulada, pedidos, reservas

SEGUNDOS_ENTRE_REVISIONES = 5

registro = logging.getLogger("expressya.vencimiento")
registro_pagos = logging.getLogger("expressya.pagos")


async def revisar_reservas_vencidas():
    while True:
        try:
            expiradas = await asyncio.to_thread(reservas.expirar_vencidas)
            if expiradas:
                registro.info("Reservas expiradas: %s", expiradas)
        except Exception:
            registro.exception("Fallo la revision de reservas vencidas")
        await asyncio.sleep(SEGUNDOS_ENTRE_REVISIONES)


async def procesar_eventos_y_recuperacion():
    while True:
        try:
            procesados = await asyncio.to_thread(pedidos.procesar_eventos_pendientes)
            if procesados:
                registro_pagos.info("Eventos EV-01 procesados: %s", procesados)
            recuperados = await asyncio.to_thread(pagos.recuperar_intentos_iniciados)
            if recuperados:
                registro_pagos.info("Intentos INICIADO recuperados a EN_VERIFICACION: %s", recuperados)
        except Exception:
            registro_pagos.exception("Fallo el ciclo de eventos y recuperacion de pagos")
        await asyncio.sleep(SEGUNDOS_ENTRE_REVISIONES)


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    tarea_vencimiento = asyncio.create_task(revisar_reservas_vencidas())
    tarea_eventos = asyncio.create_task(procesar_eventos_y_recuperacion())
    yield
    tarea_vencimiento.cancel()
    tarea_eventos.cancel()


app = FastAPI(title="ExpressYa", lifespan=ciclo_de_vida)
app.add_exception_handler(ErrorDeNegocio, responder_error_de_negocio)
app.include_router(inventario.router)
app.include_router(reservas.router)
app.include_router(pedidos.router)
app.include_router(pagos.router)
app.include_router(pasarela_simulada.router)


@app.get("/v1/salud")
def salud():
    with pool.connection() as conn:
        hora = conn.execute("SELECT clock_timestamp()").fetchone()[0]
    return {"estado": "ok", "hora_servidor": hora}


app.mount("/", StaticFiles(directory="static", html=True), name="static")

from app.modulos import cancelaciones

app.include_router(cancelaciones.router)