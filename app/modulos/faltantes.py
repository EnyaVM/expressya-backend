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
ROLES_REPORTE = ("PICKER", "PERSONAL_TIENDA")
ROLES_RESOLUCION = ("SUPERVISOR",)

FALTANTE = "FALTANTE"
DIFERENCIA = "DIFERENCIA_SIN_FALTANTE"


class ConteoSolicitud(BaseModel):
    cantidad_contada: int = Field(ge=0)
    fecha_corte: datetime


class FaltanteSolicitud(BaseModel):
    evidencia: str = Field(min_length=1, max_length=500)


class ResolucionSolicitud(BaseModel):
    conteo_id: UUID
    unidades_validadas: int = Field(ge=0)
    evidencia: str = Field(min_length=1, max_length=500)
    motivo: str = Field(min_length=1, max_length=200)


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


def registrar_movimiento(conn, darkstore_id, producto_id, tipo, motivo, usuario_id, clave, saldos: dict,
                         autorizado_por=None, **referencias) -> UUID:
    columnas = "".join(f", referencia_{k}_id" for k in referencias)
    marcas = "".join(", %s" for _ in referencias)
    movimiento_id = conn.execute(
        "INSERT INTO movimiento_inventario (darkstore_id, producto_id, tipo, motivo, usuario_id, autorizado_por, clave_operacion"
        f"{columnas}) VALUES (%s, %s, %s, %s, %s, %s, %s{marcas}) RETURNING movimiento_id",
        (darkstore_id, producto_id, tipo, motivo, usuario_id, autorizado_por, clave, *referencias.values()),
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
    abierta = discrepancia_abierta(conn, darkstore_id, producto_id)
    tipo = abierta[1] if abierta else clasificar_conteo(cantidad, e, obligaciones(conn, darkstore_id, producto_id))
    discrepancia_id = abierta[0] if abierta else None
    if tipo and not abierta:
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


def discrepancia_abierta(conn, darkstore_id, producto_id) -> tuple[UUID, str] | None:
    return conn.execute(
        "SELECT discrepancia_id, tipo FROM discrepancia "
        "WHERE darkstore_id = %s AND producto_id = %s AND estado = 'EN_VALIDACION' FOR UPDATE",
        (darkstore_id, producto_id),
    ).fetchone()


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


def reportar_faltante(conn, linea_id, usuario_id, evidencia, clave) -> dict:
    fila = conn.execute(
        "SELECT pedido_id, darkstore_id, producto_id FROM linea_pedido WHERE linea_id = %s", (linea_id,)
    ).fetchone()
    if fila is None:
        raise ErrorDeNegocio(404, "NO_ENCONTRADO", "La línea de pedido no existe.")
    pedido_id, darkstore_id, producto_id = fila
    exigir_rol(conn, usuario_id, darkstore_id, ROLES_REPORTE)
    estado_pedido = conn.execute("SELECT estado FROM pedido WHERE pedido_id = %s FOR UPDATE", (pedido_id,)).fetchone()[0]
    bloquear_inventario(conn, darkstore_id, producto_id)
    estado_linea = conn.execute("SELECT estado FROM linea_pedido WHERE linea_id = %s FOR UPDATE", (linea_id,)).fetchone()[0]
    if estado_pedido != "CONFIRMADO" or estado_linea != "ASIGNADA":
        raise ErrorDeNegocio(409, "ESTADO_INCOMPATIBLE", "Solo se reporta un faltante en una línea pagada pendiente de surtir.")
    discrepancia_id = convertir_en_faltante(conn, darkstore_id, producto_id) or abrir_discrepancia(
        conn, darkstore_id, producto_id, FALTANTE, "M05", reportado_por=usuario_id, evidencia=evidencia
    )
    registrar_movimiento(
        conn, darkstore_id, producto_id, "DISCREPANCIA", "discrepancia_fisica", usuario_id, clave, {},
        linea=linea_id, discrepancia=discrepancia_id,
    )
    marcar_faltante(conn, darkstore_id, producto_id, discrepancia_id, clave)
    return {"discrepancia_id": str(discrepancia_id), "estado": "EN_VALIDACION", "origen": "REPORTE_PICKER"}


def convertir_en_faltante(conn, darkstore_id, producto_id) -> UUID | None:
    abierta = discrepancia_abierta(conn, darkstore_id, producto_id)
    if abierta is None:
        return None
    conn.execute("UPDATE discrepancia SET tipo = %s WHERE discrepancia_id = %s", (FALTANTE, abierta[0]))
    return abierta[0]


def repartir(disponibles: int, pendientes: list[int]) -> list[tuple[int, int]]:
    reparto = []
    for pendiente in pendientes:
        asignadas = min(pendiente, max(disponibles, 0))
        disponibles -= asignadas
        reparto.append((asignadas, pendiente - asignadas))
    return reparto


def leer_discrepancia_abierta(conn, discrepancia_id) -> dict:
    fila = conn.execute(
        "SELECT darkstore_id, producto_id, tipo, estado, conteo_origen_id, fecha_deteccion FROM discrepancia "
        "WHERE discrepancia_id = %s FOR UPDATE",
        (discrepancia_id,),
    ).fetchone()
    if fila is None:
        raise ErrorDeNegocio(404, "NO_ENCONTRADO", "La discrepancia no existe.")
    if fila[3] != "EN_VALIDACION":
        raise ErrorDeNegocio(409, "ESTADO_INCOMPATIBLE", "La discrepancia ya fue resuelta.")
    return dict(zip(("darkstore_id", "producto_id", "tipo", "estado", "conteo_origen_id", "fecha_deteccion"), fila))


def conteo_vigente(conn, discrepancia: dict, conteo_id) -> int:
    fila = conn.execute(
        "SELECT cantidad_contada FROM conteo_fisico WHERE conteo_id = %s AND darkstore_id = %s AND producto_id = %s "
        "AND (conteo_id = %s OR fecha_registro >= %s)",
        (conteo_id, discrepancia["darkstore_id"], discrepancia["producto_id"], discrepancia["conteo_origen_id"], discrepancia["fecha_deteccion"]),
    ).fetchone()
    if fila is None:
        raise ErrorDeNegocio(409, "ESTADO_INCOMPATIBLE", "El conteo no es de este producto o es anterior a la discrepancia.")
    return fila[0]


def unidades_en_revision(conn, discrepancia_id) -> int:
    return conn.execute(
        "SELECT coalesce(sum(ms.cantidad), 0) FROM movimiento_inventario m JOIN movimiento_saldo ms USING (movimiento_id) "
        "WHERE m.tipo = 'DISCREPANCIA' AND m.referencia_discrepancia_id = %s AND ms.saldo = 'N'",
        (discrepancia_id,),
    ).fetchone()[0]


def lineas_pendientes(conn, darkstore_id, producto_id) -> list[tuple]:
    return conn.execute(
        "SELECT lp.linea_id, lp.pedido_id, lp.s, lp.q - lp.s - lp.k FROM linea_pedido lp JOIN pedido p USING (pedido_id) "
        "WHERE lp.darkstore_id = %s AND lp.producto_id = %s AND p.estado = 'CONFIRMADO' "
        "AND lp.estado IN ('ASIGNADA', 'NO_SURTIBLE') AND lp.s + lp.k < lp.q "
        "ORDER BY lp.fecha_compromiso, lp.linea_id",
        (darkstore_id, producto_id),
    ).fetchall()


def bloquear_pedidos_y_lineas(conn, darkstore_id, producto_id) -> tuple[int, list[tuple]]:
    pedidos = sorted({fila[1] for fila in lineas_pendientes(conn, darkstore_id, producto_id)})
    conn.execute("SELECT 1 FROM pedido WHERE pedido_id = ANY(%s) ORDER BY pedido_id FOR UPDATE", (pedidos,))
    e, _ = bloquear_inventario(conn, darkstore_id, producto_id)
    lineas = lineas_pendientes(conn, darkstore_id, producto_id)
    conn.execute("SELECT 1 FROM linea_pedido WHERE linea_id = ANY(%s) ORDER BY linea_id FOR UPDATE", ([l[0] for l in lineas],))
    return e, lineas


def validar_unidades(unidades: int, maximo: int) -> None:
    if unidades > maximo:
        raise ErrorDeNegocio(400, "SOLICITUD_INVALIDA", "Las unidades validadas superan lo que el sistema o el conteo registran.", {"maximo": maximo})


def reservadas(conn, darkstore_id, producto_id) -> int:
    return conn.execute(
        "SELECT r FROM v_saldos_inventario WHERE darkstore_id = %s AND producto_id = %s", (darkstore_id, producto_id)
    ).fetchone()[0]


def resolver_discrepancia(conn, discrepancia_id, usuario_id, solicitud: ResolucionSolicitud, clave, por_enviar: list) -> dict:
    d = leer_discrepancia_abierta(conn, discrepancia_id)
    tienda, producto = d["darkstore_id"], d["producto_id"]
    exigir_rol(conn, usuario_id, tienda, ROLES_RESOLUCION)
    contadas = conteo_vigente(conn, d, solicitud.conteo_id)
    e, lineas = bloquear_pedidos_y_lineas(conn, tienda, producto)
    en_revision = unidades_en_revision(conn, discrepancia_id)
    unidades = solicitud.unidades_validadas
    validar_unidades(unidades, max(e + en_revision, contadas))
    if unidades < reservadas(conn, tienda, producto) + sum(l[3] for l in lineas):
        retirar_de_reservas_activas(conn, tienda, producto, discrepancia_id, clave)
    reparto = repartir(unidades - reservadas(conn, tienda, producto), [l[3] for l in lineas])
    resolucion_id = registrar_resolucion(conn, discrepancia_id, d, usuario_id, solicitud)
    asignaciones = registrar_asignaciones(conn, resolucion_id, tienda, producto, lineas, reparto)
    ajustar_existencias(conn, tienda, producto, unidades - e, en_revision, usuario_id, clave, resolucion_id, discrepancia_id, solicitud.conteo_id)
    por_enviar.extend(cancelar_canceladas(conn, resolucion_id, asignaciones, usuario_id, clave))
    cerrar_discrepancia(conn, discrepancia_id, tienda, producto, [l[0] for l in lineas])
    return {
        "resolucion_id": str(resolucion_id),
        "discrepancia_id": str(discrepancia_id),
        "unidades_validadas": unidades,
        "asignaciones": asignaciones,
    }


def registrar_resolucion(conn, discrepancia_id, d: dict, usuario_id, solicitud: ResolucionSolicitud) -> UUID:
    regla = "D-05: lo separado se conserva; el resto se asigna por fecha_compromiso" if d["tipo"] == FALTANTE else "Ajuste de conteo validado (P4, P8)"
    return conn.execute(
        "INSERT INTO resolucion_faltante (discrepancia_id, darkstore_id, producto_id, supervisor_id, unidades_validadas, "
        "conteo_id, evidencia, regla_aplicada, motivo) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING resolucion_id",
        (discrepancia_id, d["darkstore_id"], d["producto_id"], usuario_id, solicitud.unidades_validadas,
         solicitud.conteo_id, solicitud.evidencia, regla, solicitud.motivo),
    ).fetchone()[0]


def registrar_asignaciones(conn, resolucion_id, tienda, producto, lineas, reparto) -> list[dict]:
    asignaciones = []
    for (linea_id, _, separadas, _), (asignadas, canceladas) in zip(lineas, reparto):
        conn.execute(
            "INSERT INTO asignacion_faltante (resolucion_id, linea_id, darkstore_id, producto_id, separadas_antes, asignadas, canceladas) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (resolucion_id, linea_id, tienda, producto, separadas, asignadas, canceladas),
        )
        asignaciones.append({"linea_id": str(linea_id), "separadas_antes": separadas, "asignadas": asignadas, "canceladas": canceladas})
    return asignaciones


def ajustar_existencias(conn, tienda, producto, cambio_e, en_revision, usuario_id, clave, resolucion_id, discrepancia_id, conteo_id) -> None:
    conn.execute(
        "UPDATE inventario SET e = e + %s, n = n - %s WHERE darkstore_id = %s AND producto_id = %s",
        (cambio_e, en_revision, tienda, producto),
    )
    registrar_movimiento(
        conn, tienda, producto, "RESOLUCION", "ajuste_conteo", usuario_id, clave, {"E": cambio_e, "N": -en_revision},
        autorizado_por=usuario_id, resolucion=resolucion_id, discrepancia=discrepancia_id, conteo=conteo_id,
    )


def cancelar_canceladas(conn, resolucion_id, asignaciones: list[dict], usuario_id, clave) -> list[UUID]:
    from app.modulos.cancelaciones import cancelar_cantidad_por_faltante

    reembolsos = [
        cancelar_cantidad_por_faltante(conn, resolucion_id, UUID(a["linea_id"]), usuario_id, clave)
        for a in asignaciones if a["canceladas"]
    ]
    return [r for r in reembolsos if r]


def cerrar_discrepancia(conn, discrepancia_id, tienda, producto, lineas: list[UUID]) -> None:
    conn.execute(
        "UPDATE linea_pedido SET estado = 'ASIGNADA' WHERE linea_id = ANY(%s) AND estado = 'NO_SURTIBLE'", (lineas,)
    )
    conn.execute("UPDATE discrepancia SET estado = 'RESUELTA' WHERE discrepancia_id = %s", (discrepancia_id,))
    conn.execute(
        "UPDATE inventario SET estado_producto = 'HABILITADO' WHERE darkstore_id = %s AND producto_id = %s", (tienda, producto)
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


@router.post("/v1/lineas-pedido/{linea_id}/faltantes", status_code=201)
def reportar_faltante_http(
    linea_id: UUID,
    cuerpo: FaltanteSolicitud,
    response: Response,
    clave_operacion: str | None = Header(default=None, alias="Clave-Operacion"),
    usuario_id: str | None = Header(default=None, alias="Usuario-Id"),
):
    clave = validar_clave_operacion(clave_operacion)
    usuario = usuario_registrado(usuario_id)
    return operar(
        "CL-10", "linea_pedido", linea_id, usuario, clave, cuerpo.model_dump(),
        lambda conn: reportar_faltante(conn, linea_id, usuario, cuerpo.evidencia, clave),
        response, 201,
    )


@router.post("/v1/discrepancias/{discrepancia_id}/resolucion")
def resolver_discrepancia_http(
    discrepancia_id: UUID,
    cuerpo: ResolucionSolicitud,
    response: Response,
    clave_operacion: str | None = Header(default=None, alias="Clave-Operacion"),
    usuario_id: str | None = Header(default=None, alias="Usuario-Id"),
):
    from app.modulos.pagos import enviar_reembolso

    clave = validar_clave_operacion(clave_operacion)
    usuario = usuario_registrado(usuario_id)
    por_enviar: list[UUID] = []
    resultado = operar(
        "CL-12", "discrepancia", discrepancia_id, usuario, clave, cuerpo.model_dump(mode="json"),
        lambda conn: resolver_discrepancia(conn, discrepancia_id, usuario, cuerpo, clave, por_enviar),
        response, 200,
    )
    for reembolso_id in por_enviar:
        enviar_reembolso(reembolso_id)
    return resultado
