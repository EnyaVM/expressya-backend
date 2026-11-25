from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Header, Response
from pydantic import BaseModel, Field

from app.db import pool
from app.errores import ErrorDeNegocio
from app.idempotencia import calcular_huella, ejecutar_idempotente, validar_clave_operacion
from app.modulos.avisos import registrar_aviso
from app.modulos.pagos import _ejecutar_con_reintento_concurrencia, _parsear_uuid_cabecera
from app.modulos.reservas import cerrar_si_sigue_activa

router = APIRouter(tags=["Faltantes (M01, M05)"])

ROLES_CONTEO = ("PICKER", "SUPERVISOR")

FALTANTE = "FALTANTE"
DIFERENCIA = "DIFERENCIA_SIN_FALTANTE"


class ConteoSolicitud(BaseModel):
    cantidad_contada: int = Field(ge=0)
    fecha_corte: datetime


def exigir_rol(conn, usuario_id: UUID, darkstore_id: UUID, roles: tuple[str, ...]) -> None:
    fila = conn.execute(
        "SELECT 1 FROM asignacion_rol WHERE usuario_id = %s AND darkstore_id = %s AND rol = ANY(%s)",
        (usuario_id, darkstore_id, list(roles)),
    ).fetchone()
    if fila is None:
        raise ErrorDeNegocio(403, "SIN_PERMISO", "Tu perfil no permite esta operación en esta tienda.", {"roles": list(roles)})


def bloquear_inventario(conn, darkstore_id: UUID, producto_id: UUID) -> tuple[int, int]:
    fila = conn.execute(
        "SELECT e, n FROM inventario WHERE darkstore_id = %s AND producto_id = %s FOR UPDATE",
        (darkstore_id, producto_id),
    ).fetchone()
    if fila is None:
        raise ErrorDeNegocio(404, "NO_ENCONTRADO", "El producto no existe en esta tienda.")
    return fila


def obligaciones(conn, darkstore_id: UUID, producto_id: UUID) -> int:
    return conn.execute(
        "SELECT r + c FROM v_saldos_inventario WHERE darkstore_id = %s AND producto_id = %s",
        (darkstore_id, producto_id),
    ).fetchone()[0]


def registrar_movimiento(conn, darkstore_id, producto_id, tipo, motivo, usuario_id, clave, saldos: dict, **referencias) -> UUID:
    columnas = ", ".join(f"referencia_{k}_id" for k in referencias)
    marcas = ", ".join(["%s"] * len(referencias))
    movimiento_id = conn.execute(
        "INSERT INTO movimiento_inventario (darkstore_id, producto_id, tipo, motivo, usuario_id, clave_operacion"
        + (f", {columnas}) VALUES (%s, %s, %s, %s, %s, %s, {marcas})" if referencias else ") VALUES (%s, %s, %s, %s, %s, %s)")
        + " RETURNING movimiento_id",
        (darkstore_id, producto_id, tipo, motivo, usuario_id, clave, *referencias.values()),
    ).fetchone()[0]
    for saldo, cantidad in saldos.items():
        if cantidad:
            conn.execute(
                "INSERT INTO movimiento_saldo (movimiento_id, saldo, cantidad) VALUES (%s, %s, %s)",
                (movimiento_id, saldo, cantidad),
            )
    return movimiento_id


def abrir_discrepancia(conn, darkstore_id, producto_id, tipo, productor, conteo_id=None, reportado_por=None, evidencia=None) -> UUID:
    origen = "CONTEO" if conteo_id else "REPORTE_PICKER"
    discrepancia_id = conn.execute(
        "INSERT INTO discrepancia (darkstore_id, producto_id, tipo, origen, conteo_origen_id, reportado_por, reporte_picker_evidencia) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING discrepancia_id",
        (darkstore_id, producto_id, tipo, origen, conteo_id, reportado_por, evidencia),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO evento_interno (tipo, productor, discrepancia_id, procesado, procesado_en) "
        "VALUES ('EV-03', %s, %s, true, clock_timestamp())",
        (productor, discrepancia_id),
    )
    return discrepancia_id


def retirar_de_reservas_activas(conn, darkstore_id, producto_id, discrepancia_id, clave) -> int:
    lineas = conn.execute(
        "SELECT lr.reserva_id, lr.cantidad, lr.precio_unitario, r.pedido_id, r.cliente_id "
        "FROM linea_reserva lr JOIN reserva r USING (reserva_id) "
        "WHERE lr.darkstore_id = %s AND lr.producto_id = %s AND lr.fecha_retiro IS NULL AND r.estado = 'ACTIVA' "
        "ORDER BY lr.reserva_id",
        (darkstore_id, producto_id),
    ).fetchall()
    for reserva_id, cantidad, precio, pedido_id, cliente_id in lineas:
        conn.execute(
            "UPDATE linea_reserva SET fecha_retiro = clock_timestamp(), discrepancia_id = %s WHERE reserva_id = %s AND producto_id = %s",
            (discrepancia_id, reserva_id, producto_id),
        )
        conn.execute("UPDATE pedido SET total_vigente = total_vigente - %s WHERE pedido_id = %s", (cantidad * precio, pedido_id))
        registrar_movimiento(
            conn, darkstore_id, producto_id, "RETIRO_RESERVA", "Producto retirado por faltante (CL-11)", None, clave,
            {"R": -cantidad}, reserva=reserva_id, discrepancia=discrepancia_id,
        )
        cerrar_si_queda_vacia(conn, reserva_id, clave)
        registrar_aviso(conn, "PRODUCTO_RETIRADO", "RESERVA", reserva_id, cliente_id=cliente_id, pedido_id=pedido_id)
    return len(lineas)


def cerrar_si_queda_vacia(conn, reserva_id, clave) -> None:
    quedan = conn.execute(
        "SELECT count(*) FROM linea_reserva WHERE reserva_id = %s AND fecha_retiro IS NULL", (reserva_id,)
    ).fetchone()[0]
    if quedan == 0:
        cerrar_si_sigue_activa(conn, reserva_id, "LIBERADA", "FALTANTE", "LIBERACION", "Reserva sin productos por faltante", clave_operacion=clave)


def marcar_faltante(conn, darkstore_id, producto_id, discrepancia_id, clave) -> None:
    conn.execute(
        "UPDATE inventario SET estado_producto = 'BLOQUEADO' WHERE darkstore_id = %s AND producto_id = %s",
        (darkstore_id, producto_id),
    )
    conn.execute(
        "UPDATE linea_pedido lp SET estado = 'NO_SURTIBLE' FROM pedido p "
        "WHERE p.pedido_id = lp.pedido_id AND p.estado = 'CONFIRMADO' "
        "AND lp.darkstore_id = %s AND lp.producto_id = %s AND lp.estado = 'ASIGNADA'",
        (darkstore_id, producto_id),
    )
    retirar_de_reservas_activas(conn, darkstore_id, producto_id, discrepancia_id, clave)


def registrar_conteo(conn, darkstore_id, producto_id, usuario_id, cantidad, fecha_corte, clave) -> dict:
    exigir_rol(conn, usuario_id, darkstore_id, ROLES_CONTEO)
    e, _ = bloquear_inventario(conn, darkstore_id, producto_id)
    conteo_id, fecha_registro = conn.execute(
        "INSERT INTO conteo_fisico (darkstore_id, producto_id, cantidad_contada, e_al_corte, fecha_corte, usuario_id) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING conteo_id, fecha_registro",
        (darkstore_id, producto_id, cantidad, e, fecha_corte, usuario_id),
    ).fetchone()
    tipo = clasificar_conteo(cantidad, e, obligaciones(conn, darkstore_id, producto_id))
    discrepancia_id = None
    if tipo:
        discrepancia_id = abrir_discrepancia(conn, darkstore_id, producto_id, tipo, "M01", conteo_id=conteo_id)
        poner_en_revision(conn, darkstore_id, producto_id, e - cantidad, usuario_id, clave, conteo_id, discrepancia_id)
        if tipo == FALTANTE:
            marcar_faltante(conn, darkstore_id, producto_id, discrepancia_id, clave)
    return {
        "conteo_id": str(conteo_id),
        "cantidad_contada": cantidad,
        "e_al_corte": e,
        "fecha_registro": fecha_registro.isoformat(),
        "discrepancia_id": str(discrepancia_id) if discrepancia_id else None,
        "tipo_discrepancia": tipo,
    }


def clasificar_conteo(contadas: int, existencias: int, comprometidas: int) -> str | None:
    if contadas == existencias:
        return None
    if contadas < comprometidas:
        return FALTANTE
    return DIFERENCIA


def poner_en_revision(conn, darkstore_id, producto_id, diferencia, usuario_id, clave, conteo_id, discrepancia_id) -> None:
    if diferencia <= 0:
        return
    conn.execute(
        "UPDATE inventario SET e = e - %s, n = n + %s WHERE darkstore_id = %s AND producto_id = %s",
        (diferencia, diferencia, darkstore_id, producto_id),
    )
    registrar_movimiento(
        conn, darkstore_id, producto_id, "DISCREPANCIA", "en_revision", usuario_id, clave,
        {"E": -diferencia, "N": diferencia}, conteo=conteo_id, discrepancia=discrepancia_id,
    )


def operar(operacion, objeto, objeto_id, usuario_id, clave, cuerpo: dict, trabajo, response: Response, estado_http: int):
    huella = calcular_huella(operacion, objeto_id, {**cuerpo, "usuario_id": str(usuario_id)})

    def intento():
        with pool.connection() as conn:
            return ejecutar_idempotente(
                conn, clave=clave, operacion=operacion, objeto=objeto, objeto_id=objeto_id,
                actor_tipo="USUARIO_OPERATIVO", usuario_id=usuario_id, huella=huella,
                trabajo=lambda: {"estado_http": estado_http, "cuerpo": trabajo(conn)},
            )

    respuesta, previa = _ejecutar_con_reintento_concurrencia(intento)
    response.status_code = respuesta["estado_http"]
    if previa:
        response.headers["Resultado-Previo"] = "true"
    return respuesta["cuerpo"]


def usuario_registrado(valor: str | None) -> UUID:
    usuario_id = _parsear_uuid_cabecera(valor, "Usuario-Id")
    with pool.connection() as conn:
        existe = conn.execute("SELECT 1 FROM usuario_operativo WHERE usuario_id = %s", (usuario_id,)).fetchone()
    if existe is None:
        raise ErrorDeNegocio(403, "SIN_PERMISO", "El usuario operativo no está registrado.")
    return usuario_id


@router.post("/v1/darkstores/{darkstore_id}/productos/{producto_id}/conteos", status_code=201)
def registrar_conteo_http(
    darkstore_id: UUID,
    producto_id: UUID,
    cuerpo: ConteoSolicitud,
    response: Response,
    clave_operacion: str | None = Header(default=None, alias="Clave-Operacion"),
    usuario_id: str | None = Header(default=None, alias="Usuario-Id"),
):
    clave = validar_clave_operacion(clave_operacion)
    usuario = usuario_registrado(usuario_id)
    return operar(
        "CL-09", "producto", producto_id, usuario, clave,
        {"darkstore_id": str(darkstore_id), **cuerpo.model_dump(mode="json")},
        lambda conn: registrar_conteo(conn, darkstore_id, producto_id, usuario, cuerpo.cantidad_contada, cuerpo.fecha_corte, clave),
        response, 201,
    )
