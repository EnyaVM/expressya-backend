"""Consultas de la interfaz. Identidad temporal igual que los contratos existentes.
El alta de clientes y selector de personal son exclusivos del entorno local de demo.
"""
import os
from uuid import UUID
from fastapi import APIRouter, Header
from psycopg.rows import dict_row
from app.db import pool
from app.errores import ErrorDeNegocio

router = APIRouter(prefix='/v1/portal', tags=['Portal (consultas de interfaz)'])


def filas(conn, sql, args=()):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, args)
        return cur.fetchall()


def exigir_demo():
    if os.getenv('EXPRESSYA_DEMO') != '1':
        raise ErrorDeNegocio(404, 'NO_ENCONTRADO', 'Entorno de demostración desactivado.')


def exigir_rol(conn, usuario_id, tienda):
    if not usuario_id or not conn.execute('SELECT 1 FROM asignacion_rol WHERE usuario_id=%s AND darkstore_id=%s', (usuario_id, tienda)).fetchone():
        raise ErrorDeNegocio(403, 'SIN_PERMISO', 'Se requiere un usuario asignado a esta tienda.')


@router.get('/tiendas')
def tiendas():
    with pool.connection() as conn:
        return filas(conn, 'SELECT * FROM dark_store ORDER BY nombre')


@router.get('/catalogo/{tienda}')
def catalogo(tienda: UUID):
    with pool.connection() as conn:
        return filas(conn, '''SELECT p.producto_id, p.nombre, p.descripcion,
            p.precio_unitario::text AS precio, v.disponibilidad
            FROM producto p JOIN v_disponibilidad_cliente v USING(producto_id)
            WHERE v.darkstore_id=%s ORDER BY p.nombre''', (tienda,))


@router.post('/clientes', status_code=201)
def nuevo_cliente():
    exigir_demo()
    with pool.connection() as conn:
        return {'cliente_id': conn.execute('INSERT INTO cliente DEFAULT VALUES RETURNING cliente_id').fetchone()[0]}


@router.get('/personal')
def personal():
    exigir_demo()
    with pool.connection() as conn:
        return filas(conn, '''SELECT u.usuario_id,u.nombre,a.darkstore_id,a.rol
            FROM usuario_operativo u JOIN asignacion_rol a USING(usuario_id)
            ORDER BY u.nombre''')


@router.get('/pedidos')
def pedidos(cliente_id: UUID = Header(alias='Cliente-Id')):
    with pool.connection() as conn:
        return filas(conn, '''SELECT p.pedido_id,p.estado,p.total_vigente::text AS total_vigente,
            p.fecha_creacion,d.nombre AS tienda,r.estado AS estado_reserva
            FROM pedido p JOIN dark_store d USING(darkstore_id) JOIN reserva r USING(reserva_id)
            WHERE p.cliente_id=%s ORDER BY p.fecha_creacion DESC LIMIT 100''', (cliente_id,))


@router.get('/operacion/{tienda}')
def operacion(tienda: UUID, usuario_id: UUID = Header(alias='Usuario-Id')):
    with pool.connection() as conn:
        exigir_rol(conn, usuario_id, tienda)
        return {
            'inventario': filas(conn, '''SELECT v.*,p.nombre,h.e_historial,h.n_historial
                FROM v_saldos_inventario v JOIN producto p USING(producto_id)
                JOIN v_reconstruccion_saldos h USING(darkstore_id,producto_id)
                WHERE v.darkstore_id=%s ORDER BY p.nombre''', (tienda,)),
            'pedidos': filas(conn, '''SELECT p.pedido_id,p.estado,p.total_vigente::text AS total_vigente,p.fecha_creacion
                FROM pedido p WHERE darkstore_id=%s ORDER BY fecha_creacion DESC LIMIT 100''', (tienda,)),
            'discrepancias': filas(conn, '''SELECT d.*,p.nombre FROM discrepancia d JOIN producto p USING(producto_id)
                WHERE darkstore_id=%s ORDER BY fecha_deteccion DESC LIMIT 100''', (tienda,))
        }


@router.get('/pedido/{pedido}')
def detalle(pedido: UUID, cliente_id: UUID | None = Header(default=None, alias='Cliente-Id'), usuario_id: UUID | None = Header(default=None, alias='Usuario-Id')):
    with pool.connection() as conn:
        datos = filas(conn, '''SELECT p.*,p.total_vigente::text AS total_vigente,d.nombre AS tienda,
            r.estado AS estado_reserva,r.t_vence,clock_timestamp() AS hora_servidor
            FROM pedido p JOIN dark_store d USING(darkstore_id) JOIN reserva r USING(reserva_id)
            WHERE p.pedido_id=%s''', (pedido,))
        if not datos:
            raise ErrorDeNegocio(404,'NO_ENCONTRADO','Pedido no encontrado.')
        p = datos[0]
        if usuario_id:
            exigir_rol(conn, usuario_id, p['darkstore_id'])
        elif cliente_id != p['cliente_id']:
            raise ErrorDeNegocio(404,'NO_ENCONTRADO','Pedido no encontrado.')
        p['lineas'] = filas(conn, '''SELECT lr.producto_id,pr.nombre,lr.cantidad,lr.precio_unitario::text AS precio,
            lp.linea_id,lp.s,lp.k,lp.estado,lr.fecha_retiro
            FROM linea_reserva lr JOIN producto pr USING(producto_id)
            LEFT JOIN linea_pedido lp ON lp.reserva_id=lr.reserva_id AND lp.producto_id=lr.producto_id
            WHERE lr.reserva_id=%s ORDER BY pr.nombre''', (p['reserva_id'],))
        p['pagos'] = filas(conn, '''SELECT intento_id,estado,monto::text AS monto,fecha_inicio,referencia_externa,
            reembolso_actual_id FROM intento_pago WHERE pedido_id=%s ORDER BY fecha_inicio''', (pedido,))
        p['reembolsos'] = filas(conn, '''SELECT reembolso_id,intento_id,monto::text AS monto,motivo,
            fecha_solicitud,fecha_envio,fecha_confirmacion,fecha_fallo_confirmado
            FROM reembolso WHERE pedido_id=%s ORDER BY fecha_solicitud''', (pedido,))
        p['avisos'] = filas(conn, '''SELECT tipo,fecha FROM aviso WHERE pedido_id=%s AND destinatario_tipo='CLIENTE' ORDER BY fecha''', (pedido,))
        return p


@router.get('/movimientos/{tienda}/{producto}')
def movimientos(tienda: UUID, producto: UUID, usuario_id: UUID = Header(alias='Usuario-Id')):
    with pool.connection() as conn:
        exigir_rol(conn,usuario_id,tienda)
        return filas(conn, '''SELECT m.movimiento_id,m.fecha,m.tipo,m.motivo,m.usuario_id,m.autorizado_por,
            m.referencia_linea_id,m.referencia_reserva_id,ms.saldo,ms.cantidad
            FROM movimiento_inventario m LEFT JOIN movimiento_saldo ms USING(movimiento_id)
            WHERE m.darkstore_id=%s AND m.producto_id=%s ORDER BY fecha DESC,m.movimiento_id,ms.saldo LIMIT 250''', (tienda,producto))
