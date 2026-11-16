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


async def revisar_reservas_vencidas():
    while True:
        try:
            expiradas = await asyncio.to_thread(reservas.expirar_vencidas)
            if expiradas:
                registro.info("Reservas expiradas: %s", expiradas)
        except Exception:
            registro.exception("Fallo la revision de reservas vencidas")
        await asyncio.sleep(SEGUNDOS_ENTRE_REVISIONES)


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    tarea = asyncio.create_task(revisar_reservas_vencidas())
    yield
    tarea.cancel()


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