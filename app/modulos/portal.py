"""Consultas de la interfaz. La identidad llega de la sesión (app/seguridad.py)."""
from uuid import UUID
from fastapi import APIRouter, Header, Request
from psycopg.rows import dict_row
from app.db import pool
from app.errores import ErrorDeNegocio
from app.seguridad import es_administracion

router = APIRouter(prefix='/v1/portal', tags=['Portal (consultas de interfaz)'])


def filas(conn, sql, args=()):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, args)
        return cur.fetchall()


def exigir_rol(conn, request, usuario_id, tienda):
    if es_administracion(request):
        return
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


@router.get('/pedidos')
def pedidos(cliente_id: UUID | None = Header(default=None, alias='Cliente-Id')):
    if cliente_id is None:
        raise ErrorDeNegocio(401, 'NO_AUTENTICADO', 'Inicia sesión para ver tus pedidos.')
    with pool.connection() as conn:
        return filas(conn, '''SELECT p.pedido_id,p.estado,p.total_vigente::text AS total_vigente,
            p.fecha_creacion,d.nombre AS tienda,r.estado AS estado_reserva
            FROM pedido p JOIN dark_store d USING(darkstore_id) JOIN reserva r USING(reserva_id)
            WHERE p.cliente_id=%s ORDER BY p.fecha_creacion DESC LIMIT 100''', (cliente_id,))


@router.get('/operacion/{tienda}')
def operacion(tienda: UUID, request: Request, usuario_id: UUID | None = Header(default=None, alias='Usuario-Id')):
    with pool.connection() as conn:
        exigir_rol(conn, request, usuario_id, tienda)
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
def detalle(pedido: UUID, request: Request, cliente_id: UUID | None = Header(default=None, alias='Cliente-Id'), usuario_id: UUID | None = Header(default=None, alias='Usuario-Id')):
    with pool.connection() as conn:
        datos = filas(conn, '''SELECT p.*,p.total_vigente::text AS total_vigente,d.nombre AS tienda,
            r.estado AS estado_reserva,r.t_vence,clock_timestamp() AS hora_servidor
            FROM pedido p JOIN dark_store d USING(darkstore_id) JOIN reserva r USING(reserva_id)
            WHERE p.pedido_id=%s''', (pedido,))
        if not datos:
            raise ErrorDeNegocio(404,'NO_ENCONTRADO','Pedido no encontrado.')
        p = datos[0]
        if usuario_id or es_administracion(request):
            exigir_rol(conn, request, usuario_id, p['darkstore_id'])
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
def movimientos(tienda: UUID, producto: UUID, request: Request, usuario_id: UUID | None = Header(default=None, alias='Usuario-Id')):
    with pool.connection() as conn:
        exigir_rol(conn, request, usuario_id, tienda)
        return filas(conn, '''SELECT m.movimiento_id,m.fecha,m.tipo,m.motivo,m.usuario_id,m.autorizado_por,
            m.referencia_linea_id,m.referencia_reserva_id,ms.saldo,ms.cantidad
            FROM movimiento_inventario m LEFT JOIN movimiento_saldo ms USING(movimiento_id)
            WHERE m.darkstore_id=%s AND m.producto_id=%s ORDER BY fecha DESC,m.movimiento_id,ms.saldo LIMIT 250''', (tienda,producto))


@router.get('/pendientes/{tienda}')
def pendientes(tienda: UUID, request: Request, usuario_id: UUID | None = Header(default=None, alias='Usuario-Id')):
    with pool.connection() as conn:
        exigir_rol(conn, request, usuario_id, tienda)
        return {
            'conteos': filas(conn, """SELECT c.conteo_id,c.producto_id,p.nombre,c.cantidad_contada,c.e_al_corte,c.fecha_registro
                FROM conteo_fisico c JOIN producto p USING(producto_id)
                WHERE c.darkstore_id=%s ORDER BY c.fecha_registro DESC LIMIT 50""", (tienda,)),
            'discrepancias': filas(conn, """SELECT d.discrepancia_id,d.producto_id,p.nombre,d.tipo,d.origen,d.estado,d.fecha_deteccion
                FROM discrepancia d JOIN producto p USING(producto_id)
                WHERE d.darkstore_id=%s ORDER BY d.estado DESC,d.fecha_deteccion DESC LIMIT 50""", (tienda,)),
            'danos': filas(conn, """SELECT m.movimiento_id,m.producto_id,p.nombre,s.cantidad,m.motivo,m.fecha,
                s.cantidad-COALESCE((SELECT SUM(i.cantidad) FROM inspeccion_dano i WHERE i.movimiento_dano_id=m.movimiento_id),0)::int AS por_inspeccionar
                FROM movimiento_inventario m JOIN movimiento_saldo s ON s.movimiento_id=m.movimiento_id AND s.saldo='N'
                JOIN producto p ON p.producto_id=m.producto_id
                WHERE m.darkstore_id=%s AND m.tipo='DANO' ORDER BY m.fecha DESC LIMIT 50""", (tienda,)),
            'inspecciones': filas(conn, """SELECT i.inspeccion_id,i.producto_id,p.nombre,i.cantidad,i.resultado,i.fecha,
                i.inspeccionado_por,EXISTS(SELECT 1 FROM movimiento_inventario m WHERE m.referencia_inspeccion_id=i.inspeccion_id) AS resuelta
                FROM inspeccion_dano i JOIN producto p USING(producto_id)
                WHERE i.darkstore_id=%s ORDER BY i.fecha DESC LIMIT 50""", (tienda,)),
            'avisos': filas(conn, """SELECT a.tipo,a.pedido_id,a.fecha,a.entregado_en FROM aviso a JOIN pedido pe USING(pedido_id)
                WHERE a.destinatario_tipo='RESPONSABLE' AND pe.darkstore_id=%s ORDER BY a.fecha DESC LIMIT 50""", (tienda,)),
        }
