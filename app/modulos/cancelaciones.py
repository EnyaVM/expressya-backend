
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from decimal import Decimal
from typing import Literal

from app.modulos.reembolsos import reembolsar_cancelacion
from app.modulos.avisos import notificar_cancelacion

router = APIRouter(prefix="/cancelaciones", tags=["Cancelaciones"])


class SolicitudCancelacion(BaseModel):
    pedido_id: int
    linea_id: int
    cantidad: int = Field(gt=0)
    motivo: Literal["FALTANTE", "CLIENTE"]
    clave_operacion: str = Field(min_length=8)


@router.post("/")
def cancelar_producto(solicitud: SolicitudCancelacion):
    from app.db import pool

    with pool.connection() as conn:
        with conn.transaction():

            # Bloquear el pedido para evitar cancelaciones simultáneas.
            pedido = conn.execute("""
                SELECT pedido_id, estado
                FROM pedido
                WHERE pedido_id = %s
                FOR UPDATE
            """, (solicitud.pedido_id,)).fetchone()

            if not pedido:
                raise HTTPException(404, "PEDIDO_NO_ENCONTRADO")

            # CL-13: el cliente no puede cancelar si
            # la preparación ya comenzó.
            if (
                solicitud.motivo == "CLIENTE"
                and pedido["estado"] == "PREPARACION_INICIADA"
            ):
                raise HTTPException(
                    status_code=409,
                    detail="PREPARACION_INICIADA"
                )

            # Bloquear la línea del pedido.
            linea = conn.execute("""
                SELECT linea_id, pedido_id, q, s, k, estado
                FROM linea_pedido
                WHERE linea_id = %s
                  AND pedido_id = %s
                FOR UPDATE
            """, (
                solicitud.linea_id,
                solicitud.pedido_id
            )).fetchone()

            if not linea:
                raise HTTPException(404, "LINEA_NO_ENCONTRADA")

            # q = cantidad original
            # s = cantidad surtida
            # k = cantidad cancelada
            cantidad_disponible = linea["q"] - linea["s"] - linea["k"]

            if solicitud.cantidad > cantidad_disponible:
                raise HTTPException(
                    409,
                    "CANTIDAD_NO_DISPONIBLE_PARA_CANCELAR"
                )

            # Identificar operaciones repetidas.
            operacion = conn.execute("""
                SELECT resultado
                FROM registro_operacion
                WHERE clave_operacion = %s
                FOR UPDATE
            """, (solicitud.clave_operacion,)).fetchone()

            if operacion:
                return {
                    "mensaje": "OPERACION_YA_PROCESADA",
                    "resultado": operacion["resultado"]
                }

            # Actualizar la cantidad cancelada.
            conn.execute("""
                UPDATE linea_pedido
                SET k = k + %s
                WHERE linea_id = %s
            """, (
                solicitud.cantidad,
                solicitud.linea_id
            ))

            # Calcular el importe de la cancelación.
            precio = conn.execute("""
                SELECT precio_unitario
                FROM producto p
                JOIN linea_pedido lp
                  ON lp.producto_id = p.producto_id
                WHERE lp.linea_id = %s
            """, (solicitud.linea_id,)).fetchone()

            importe = (
                Decimal(str(precio["precio_unitario"]))
                * solicitud.cantidad
            )

            # CL-16: utilizar el proceso de reembolso de C2.
            reembolso = reembolsar_cancelacion(
                conn=conn,
                pedido_id=solicitud.pedido_id,
                importe=importe,
                clave_operacion=solicitud.clave_operacion,
                motivo=solicitud.motivo
            )

            # CL-18: informar al cliente.
            notificar_cancelacion(
                conn=conn,
                pedido_id=solicitud.pedido_id,
                cantidad=solicitud.cantidad,
                motivo=solicitud.motivo,
                importe=importe
            )

            resultado = {
                "pedido_id": solicitud.pedido_id,
                "linea_id": solicitud.linea_id,
                "cantidad_cancelada": solicitud.cantidad,
                "motivo": solicitud.motivo,
                "importe_reembolsado": str(importe),
                "reembolso": reembolso
            }

            conn.execute("""
                INSERT INTO registro_operacion
                    (clave_operacion, operacion, actor, resultado)
                VALUES (%s, 'CANCELACION', %s, %s)
            """, (
                solicitud.clave_operacion,
                solicitud.motivo,
                str(resultado)
            ))

            return resultado
