from fastapi import FastAPI, HTTPException
import psycopg
from app.database import get_db_connection

app = FastAPI(
    title="ExpressYa Backend - Núcleo C1",
    version="1.1",
    description="Sistema de comercio electrónico y gestión de inventario para 5 Dark Stores[cite: 1, 6]."
)

@app.get("/")
def read_root():
    return {"status": "online", "proyecto": "ExpressYa - Desafío C1 Activo"}

@app.get("/v1/darkstores/{darkstore_id}/productos/{producto_id}/disponibilidad")
def consultar_disponibilidad(darkstore_id: str, producto_id: str):
    """
    Consulta el saldo disponible (D = E - R - C) para un producto en una Dark Store específica.
    """
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT e, r, c, (e - r - c) AS disponible, estado_producto
                FROM expressya.inventario
                WHERE darkstore_id = %s AND producto_id = %s;
            """, (darkstore_id, producto_id))
            row = cur.fetchone()
            
            if not row:
                raise HTTPException(status_code=404, producto="Producto no encontrado en inventario")
            
            e, r, c, disponible, estado = row
            return {
                "darkstore_id": darkstore_id,
                "producto_id": producto_id,
                "existencia_fisica_e": e,
                "reservado_r": r,
                "comprometido_c": c,
                "disponible_d": disponible,
                "estado_producto": estado
            }
