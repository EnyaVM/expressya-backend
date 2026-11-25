from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.db import pool
from app.main import app
from escenarios import nuevo_cliente

client = TestClient(app)


def nuevo_usuario(darkstore_id: UUID, *roles: str) -> UUID:
    usuario_id = uuid4()
    with pool.connection() as conn:
        conn.execute("INSERT INTO usuario_operativo (usuario_id, nombre) VALUES (%s, %s)", (usuario_id, "Usuario de prueba"))
        for rol in roles:
            conn.execute("INSERT INTO asignacion_rol (usuario_id, darkstore_id, rol) VALUES (%s, %s, %s)", (usuario_id, darkstore_id, rol))
    return usuario_id


def reservar(darkstore_id: UUID, producto_id: UUID, cantidad: int) -> dict:
    respuesta = client.post(
        "/v1/checkouts",
        headers={"Clave-Operacion": str(uuid4()), "Cliente-Id": str(nuevo_cliente())},
        json={"darkstore_id": str(darkstore_id), "lineas": [{"producto_id": str(producto_id), "cantidad": cantidad}]},
    )
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()


def contar(darkstore_id: UUID, producto_id: UUID, usuario_id: UUID, cantidad: int, clave: UUID | None = None):
    return client.post(
        f"/v1/darkstores/{darkstore_id}/productos/{producto_id}/conteos",
        headers={"Clave-Operacion": str(clave or uuid4()), "Usuario-Id": str(usuario_id)},
        json={"cantidad_contada": cantidad, "fecha_corte": "2026-11-24T12:00:00-06:00"},
    )


def reportar_faltante(linea_id: UUID, usuario_id: UUID, clave: UUID | None = None, evidencia: str = "Hueco vacío en el anaquel"):
    return client.post(
        f"/v1/lineas-pedido/{linea_id}/faltantes",
        headers={"Clave-Operacion": str(clave or uuid4()), "Usuario-Id": str(usuario_id)},
        json={"evidencia": evidencia},
    )


def resolver(discrepancia_id, usuario_id: UUID, conteo_id, unidades: int, clave: UUID | None = None, evidencia: str = "Recuento con foto del anaquel"):
    return client.post(
        f"/v1/discrepancias/{discrepancia_id}/resolucion",
        headers={"Clave-Operacion": str(clave or uuid4()), "Usuario-Id": str(usuario_id)},
        json={"conteo_id": str(conteo_id), "unidades_validadas": unidades, "evidencia": evidencia, "motivo": "Conciliación del conteo"},
    )


def inventario(darkstore_id: UUID, producto_id: UUID) -> dict:
    with pool.connection() as conn:
        e, n, r, c, d, estado = conn.execute(
            "SELECT e, n, r, c, d, estado_producto FROM v_saldos_inventario WHERE darkstore_id = %s AND producto_id = %s",
            (darkstore_id, producto_id),
        ).fetchone()
    return {"e": e, "n": n, "r": r, "c": c, "d": d, "estado": estado}


def discrepancias(darkstore_id: UUID, producto_id: UUID) -> list[tuple]:
    with pool.connection() as conn:
        return conn.execute(
            "SELECT tipo, origen, estado FROM discrepancia WHERE darkstore_id = %s AND producto_id = %s ORDER BY fecha_deteccion",
            (darkstore_id, producto_id),
        ).fetchall()


def eventos_ev03(darkstore_id: UUID, producto_id: UUID) -> list[tuple]:
    with pool.connection() as conn:
        return conn.execute(
            "SELECT e.tipo, e.productor FROM evento_interno e JOIN discrepancia d USING (discrepancia_id) "
            "WHERE d.darkstore_id = %s AND d.producto_id = %s",
            (darkstore_id, producto_id),
        ).fetchall()


def historial_cuadra(darkstore_id: UUID, producto_id: UUID) -> bool:
    with pool.connection() as conn:
        e, n, eh, nh = conn.execute(
            "SELECT e, n, e_historial, n_historial FROM v_reconstruccion_saldos WHERE darkstore_id = %s AND producto_id = %s",
            (darkstore_id, producto_id),
        ).fetchone()
    return (e, n) == (eh, nh)
