from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.db import pool

router = APIRouter(prefix="/v1", tags=["M01 Inventario"])


@router.get("/darkstores/{darkstore_id}/productos/{producto_id}/disponibilidad")
def consultar_disponibilidad(darkstore_id: UUID, producto_id: UUID):
    with pool.connection() as conn:
        fila = conn.execute(
            "SELECT disponibilidad FROM v_disponibilidad_cliente WHERE darkstore_id = %s AND producto_id = %s",
            (darkstore_id, producto_id),
        ).fetchone()
    if fila is None:
        raise HTTPException(status_code=404, detail="NO_ENCONTRADO")
    return {"disponibilidad": fila[0]}
