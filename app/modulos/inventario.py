from uuid import UUID

from fastapi import APIRouter

from app.db import pool
from app.errores import ErrorDeNegocio

router = APIRouter(prefix="/v1", tags=["M01 Inventario"])


@router.get("/darkstores/{darkstore_id}/productos/{producto_id}/disponibilidad")
def consultar_disponibilidad(darkstore_id: UUID, producto_id: UUID):
    with pool.connection() as conn:
        fila = conn.execute(
            "SELECT disponibilidad FROM v_disponibilidad_cliente WHERE darkstore_id = %s AND producto_id = %s",
            (darkstore_id, producto_id),
        ).fetchone()
    if fila is None:
        raise ErrorDeNegocio(404, "NO_ENCONTRADO", "El producto no existe en esta Dark Store")
    return {"disponibilidad": fila[0]}
