from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.db import pool
from app.modulos import inventario, pagos, pasarela_simulada, pedidos, reservas

app = FastAPI(title="ExpressYa")
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
