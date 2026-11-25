import hashlib
import json
from collections.abc import Callable
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from app.db import pool
from app.errores import ErrorDeNegocio, RutaReserva
from app.idempotencia import clave_tomada_por_un_pago

router = APIRouter(prefix="/v1", tags=["M02 Reservas"], route_class=RutaReserva)


class LineaSolicitud(BaseModel):
    producto_id: UUID
    cantidad: int = Field(ge=1)


class CheckoutSolicitud(BaseModel):
    darkstore_id: UUID
    lineas: list[LineaSolicitud] = Field(min_length=1)


class CierreSolicitud(BaseModel):
    motivo: Literal["CANCELACION_CLIENTE", "REINICIO_CHECKOUT"]


def verificar_disponibilidad(conn, darkstore_id: UUID, lineas: list[LineaSolicitud]) -> dict:
    pedidas = {linea.producto_id: linea.cantidad for linea in lineas}
    if len(pedidas) != len(lineas):
        raise ErrorDeNegocio(400, "SOLICITUD_INVALIDA", "Un producto aparece mas de una vez en el pedido")

    conn.execute(
        "SELECT producto_id FROM inventario "
        "WHERE darkstore_id = %s AND producto_id = ANY(%s) "
        "ORDER BY producto_id FOR UPDATE",
        (darkstore_id, list(pedidas)),
    )

    filas = conn.execute(
        "SELECT s.producto_id, s.estado_producto, s.d, p.precio_unitario "
        "FROM v_saldos_inventario s JOIN producto p USING (producto_id) "
        "WHERE s.darkstore_id = %s AND s.producto_id = ANY(%s) "
        "ORDER BY s.producto_id",
        (darkstore_id, list(pedidas)),
    ).fetchall()
    if len(filas) != len(pedidas):
        raise ErrorDeNegocio(404, "NO_ENCONTRADO", "Algun producto no existe en esta Dark Store")

    precios = {}
    for producto_id, estado, d, precio in filas:
        if estado != "HABILITADO":
            raise ErrorDeNegocio(409, "PRODUCTO_BLOQUEADO", "El producto esta bloqueado", {"producto_id": str(producto_id)})
        if d < pedidas[producto_id]:
            raise ErrorDeNegocio(
                409, "SIN_DISPONIBLE", "No hay unidades suficientes",
                {"producto_id": str(producto_id)},
            )
        precios[producto_id] = precio
    return precios


def cerrar_si_sigue_activa(conn, reserva_id: UUID, estado: str, motivo_cierre: str, tipo_movimiento: str,
                           motivo_movimiento: str, solo_si_vencida: bool = False,
                           clave_operacion: UUID | None = None) -> bool:
    fila = conn.execute("SELECT pedido_id FROM reserva WHERE reserva_id = %s", (reserva_id,)).fetchone()
    if fila is None:
        return False
    pedido_id = fila[0]

    conn.execute("SELECT 1 FROM pedido WHERE pedido_id = %s FOR UPDATE", (pedido_id,))

    condicion_de_reloj = " AND clock_timestamp() >= t_vence" if solo_si_vencida else ""
    cerrada = conn.execute(
        "UPDATE reserva SET estado = %s, motivo_cierre = %s "
        "WHERE reserva_id = %s AND estado = 'ACTIVA'" + condicion_de_reloj + " RETURNING darkstore_id",
        (estado, motivo_cierre, reserva_id),
    ).fetchone()
    if cerrada is None:
        return False
    darkstore_id = cerrada[0]

    conn.execute("UPDATE pedido SET estado = 'SIN_COMPRA' WHERE pedido_id = %s", (pedido_id,))

    lineas = conn.execute(
        "SELECT producto_id, cantidad FROM linea_reserva "
        "WHERE reserva_id = %s AND fecha_retiro IS NULL ORDER BY producto_id",
        (reserva_id,),
    ).fetchall()
    clave_operacion = clave_operacion or uuid4()
    for producto_id, cantidad in lineas:
        movimiento_id = conn.execute(
            "INSERT INTO movimiento_inventario "
            "(darkstore_id, producto_id, tipo, motivo, clave_operacion, referencia_reserva_id) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING movimiento_id",
            (darkstore_id, producto_id, tipo_movimiento, motivo_movimiento, clave_operacion, reserva_id),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO movimiento_saldo (movimiento_id, saldo, cantidad) VALUES (%s, 'R', %s)",
            (movimiento_id, -cantidad),
        )
    return True


def leer_reserva(conn, reserva_id: UUID) -> dict:
    fila = conn.execute(
        "SELECT reserva_id, pedido_id, estado, t_creacion, t_vence FROM reserva WHERE reserva_id = %s",
        (reserva_id,),
    ).fetchone()
    lineas = conn.execute(
        "SELECT producto_id, cantidad, precio_unitario FROM linea_reserva WHERE reserva_id = %s ORDER BY producto_id",
        (reserva_id,),
    ).fetchall()
    return {
        "reserva_id": str(fila[0]),
        "pedido_id": str(fila[1]),
        "estado": fila[2],
        "t_creacion": fila[3].isoformat(),
        "t_vence": fila[4].isoformat(),
        "lineas": [
            {"producto_id": str(p), "cantidad": c, "precio_unitario": f"{precio:.2f}"}
            for p, c, precio in lineas
        ],
    }


def liberar_reserva(conn, cliente_id: UUID, reserva_id: UUID, motivo: str, clave_operacion: UUID | None = None) -> dict:
    duenio = conn.execute("SELECT cliente_id FROM reserva WHERE reserva_id = %s", (reserva_id,)).fetchone()
    if duenio is None or duenio[0] != cliente_id:
        raise ErrorDeNegocio(404, "NO_ENCONTRADO", "La reserva no existe")

    liberada = cerrar_si_sigue_activa(
        conn, reserva_id, "LIBERADA", motivo, "LIBERACION", "Liberacion de la reserva por el cliente",
        clave_operacion=clave_operacion,
    )
    if not liberada:
        raise ErrorDeNegocio(409, "ESTADO_INCOMPATIBLE", "La reserva ya no esta ACTIVA")
    return leer_reserva(conn, reserva_id)


def expirar_reserva(conn, reserva_id: UUID) -> bool:
    return cerrar_si_sigue_activa(
        conn, reserva_id, "EXPIRADA", "VENCIMIENTO", "EXPIRACION",
        "Vencimiento de la reserva (300 s)", solo_si_vencida=True,
    )


def expirar_vencidas(limite: int = 100) -> int:
    with pool.connection() as conn:
        vencidas = conn.execute(
            "SELECT reserva_id FROM reserva "
            "WHERE estado = 'ACTIVA' AND t_vence <= clock_timestamp() ORDER BY t_vence LIMIT %s",
            (limite,),
        ).fetchall()

    expiradas = 0
    for (reserva_id,) in vencidas:
        with pool.connection() as conn:
            if expirar_reserva(conn, reserva_id):
                expiradas += 1
    return expiradas


def exigir_reserva_vigente(conn, reserva_id: UUID) -> None:
    fila = conn.execute(
        "SELECT estado, t_vence, clock_timestamp() >= t_vence FROM reserva WHERE reserva_id = %s",
        (reserva_id,),
    ).fetchone()
    if fila is None:
        raise ErrorDeNegocio(404, "NO_ENCONTRADO", "La reserva no existe")
    estado, t_vence, ya_vencio = fila
    if estado != "ACTIVA":
        raise ErrorDeNegocio(409, "ESTADO_INCOMPATIBLE", "La reserva ya no esta ACTIVA", {"estado": estado})
    if ya_vencio:
        raise ErrorDeNegocio(409, "RESERVA_VENCIDA", "La reserva ya vencio", {"t_vence": t_vence.isoformat()})


def crear_checkout(conn, cliente_id: UUID, darkstore_id: UUID, lineas: list[LineaSolicitud], clave_operacion: UUID) -> dict:
    existe = conn.execute("SELECT 1 FROM cliente WHERE cliente_id = %s FOR UPDATE", (cliente_id,)).fetchone()
    if existe is None:
        raise ErrorDeNegocio(401, "NO_AUTENTICADO", "El cliente no existe")

    activa = conn.execute(
        "SELECT reserva_id FROM reserva WHERE cliente_id = %s AND estado = 'ACTIVA'", (cliente_id,)
    ).fetchone()
    if activa is not None:
        ya_vencida = expirar_reserva(conn, activa[0])
        if not ya_vencida:
            raise ErrorDeNegocio(409, "CHECKOUT_ACTIVO", "El cliente ya tiene un checkout activo")

    precios = verificar_disponibilidad(conn, darkstore_id, lineas)

    pedido_id = uuid4()
    reserva_id = uuid4()
    total = sum(precios[linea.producto_id] * linea.cantidad for linea in lineas)

    conn.execute(
        "INSERT INTO pedido (pedido_id, reserva_id, cliente_id, darkstore_id, total_vigente) "
        "VALUES (%s, %s, %s, %s, %s)",
        (pedido_id, reserva_id, cliente_id, darkstore_id, total),
    )
    t_creacion, t_vence = conn.execute(
        "INSERT INTO reserva (reserva_id, pedido_id, cliente_id, darkstore_id) "
        "VALUES (%s, %s, %s, %s) RETURNING t_creacion, t_vence",
        (reserva_id, pedido_id, cliente_id, darkstore_id),
    ).fetchone()

    for linea in lineas:
        conn.execute(
            "INSERT INTO linea_reserva (reserva_id, producto_id, darkstore_id, cantidad, precio_unitario) "
            "VALUES (%s, %s, %s, %s, %s)",
            (reserva_id, linea.producto_id, darkstore_id, linea.cantidad, precios[linea.producto_id]),
        )
        movimiento_id = conn.execute(
            "INSERT INTO movimiento_inventario "
            "(darkstore_id, producto_id, tipo, motivo, clave_operacion, referencia_reserva_id) "
            "VALUES (%s, %s, 'RESERVA', 'Reserva de checkout', %s, %s) RETURNING movimiento_id",
            (darkstore_id, linea.producto_id, clave_operacion, reserva_id),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO movimiento_saldo (movimiento_id, saldo, cantidad) VALUES (%s, 'R', %s)",
            (movimiento_id, linea.cantidad),
        )

    return {
        "pedido": {
            "pedido_id": str(pedido_id),
            "estado": "PENDIENTE_DE_PAGO",
            "total_vigente": f"{total:.2f}",
            "ajuste_pendiente": False,
        },
        "reserva": {
            "reserva_id": str(reserva_id),
            "pedido_id": str(pedido_id),
            "estado": "ACTIVA",
            "t_creacion": t_creacion.isoformat(),
            "t_vence": t_vence.isoformat(),
            "lineas": [
                {
                    "producto_id": str(linea.producto_id),
                    "cantidad": linea.cantidad,
                    "precio_unitario": f"{precios[linea.producto_id]:.2f}",
                }
                for linea in lineas
            ],
        },
    }


def _huella_reserva(operacion: str, cliente_id: UUID, reserva_id: UUID | None, solicitud: BaseModel) -> bytes:
    peticion = {"operacion": operacion, "cliente_id": str(cliente_id),
                "reserva_id": str(reserva_id) if reserva_id else None, "cuerpo": solicitud.model_dump(mode="json")}
    return hashlib.sha256(json.dumps(peticion, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).digest()


def _respuesta_conservada(conn, clave: UUID, huella: bytes) -> JSONResponse | None:
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s::text, 0))", (clave,))
    previo = conn.execute("SELECT huella_solicitud, respuesta FROM registro_operacion WHERE clave_operacion = %s",
                          (clave,)).fetchone()
    if previo is None:
        if conn.execute("SELECT 1 FROM movimiento_inventario WHERE clave_operacion = %s LIMIT 1", (clave,)).fetchone():
            raise ErrorDeNegocio(409, "CONFLICTO_DE_CLAVE", "La clave ya tiene movimientos sin respuesta conservada")
        if clave_tomada_por_un_pago(conn, clave):
            raise ErrorDeNegocio(409, "CONFLICTO_DE_CLAVE", "La clave ya fue utilizada por un pago")
        return None
    if previo[0] != huella:
        raise ErrorDeNegocio(409, "CONFLICTO_DE_CLAVE", "La clave pertenece a otra solicitud")
    return JSONResponse(previo[1]["cuerpo"], status_code=previo[1]["estado_http"], headers={"Resultado-Previo": "true"})


RESERVA_DE = {
    "CL-02": ("cliente", lambda cuerpo: cuerpo["reserva"], {}),
    "CL-03": ("reserva", lambda cuerpo: cuerpo, {"estado": "ACTIVA"}),
}


def _ejecutar_reserva(conn, operacion: str, ejecutar: Callable[[], dict], estado_http: int, objeto_id: UUID) -> dict:
    objeto, reserva_de, anterior = RESERVA_DE[operacion]
    try:
        with conn.transaction():
            cuerpo = ejecutar()
    except ErrorDeNegocio as error:
        if error.estado_http == 401:
            raise
        return {"estado_http": error.estado_http, "cuerpo": error.cuerpo(), "motivo": error.codigo,
                "objeto": objeto, "objeto_id": objeto_id, "anterior": {}, "resultante": {}}
    reserva = reserva_de(cuerpo)
    return {"estado_http": estado_http, "cuerpo": cuerpo, "motivo": None, "objeto": "reserva",
            "objeto_id": reserva["reserva_id"], "anterior": anterior, "resultante": {"estado": reserva["estado"]}}


def _responder_operacion_reserva(conn, operacion: str, clave: UUID, cliente_id: UUID,
                                reserva_id: UUID | None, solicitud: BaseModel, estado_http: int,
                                ejecutar: Callable[[], dict]) -> JSONResponse:
    huella = _huella_reserva(operacion, cliente_id, reserva_id, solicitud)
    conservada = _respuesta_conservada(conn, clave, huella)
    if conservada is not None:
        return conservada
    r = _ejecutar_reserva(conn, operacion, ejecutar, estado_http, reserva_id or cliente_id)
    conn.execute(
        "INSERT INTO registro_operacion (clave_operacion, operacion, objeto, objeto_id, huella_solicitud, estado_anterior, "
        "estado_resultante, actor_tipo, resultado, respuesta, motivo_rechazo) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, 'CLIENTE', %s, %s, %s)",
        (clave, operacion, r["objeto"], r["objeto_id"], huella, Jsonb(r["anterior"]), Jsonb(r["resultante"]),
         "RECHAZADA" if r["motivo"] else "APLICADA", Jsonb({"estado_http": r["estado_http"], "cuerpo": r["cuerpo"]}), r["motivo"]),
    )
    return JSONResponse(r["cuerpo"], status_code=r["estado_http"])


def leer_clave_operacion(clave: UUID = Header(alias="Clave-Operacion")) -> UUID:
    if clave.version != 4:
        raise ErrorDeNegocio(400, "SOLICITUD_INVALIDA", "Clave-Operacion debe ser UUID v4")
    return clave


@router.post("/checkouts", status_code=201)
def crear_checkout_http(
    solicitud: CheckoutSolicitud,
    clave_operacion: UUID = Depends(leer_clave_operacion),
    cliente_id: UUID = Header(alias="Cliente-Id"),
):
    with pool.connection() as conn:
        return _responder_operacion_reserva(
            conn, "CL-02", clave_operacion, cliente_id, None, solicitud, 201,
            lambda: crear_checkout(conn, cliente_id, solicitud.darkstore_id, solicitud.lineas, clave_operacion),
        )


@router.post("/reservas/{reserva_id}/cierre")
def cerrar_reserva_http(
    reserva_id: UUID,
    solicitud: CierreSolicitud,
    clave_operacion: UUID = Depends(leer_clave_operacion),
    cliente_id: UUID = Header(alias="Cliente-Id"),
):
    with pool.connection() as conn:
        return _responder_operacion_reserva(
            conn, "CL-03", clave_operacion, cliente_id, reserva_id, solicitud, 200,
            lambda: liberar_reserva(conn, cliente_id, reserva_id, solicitud.motivo, clave_operacion),
        )
