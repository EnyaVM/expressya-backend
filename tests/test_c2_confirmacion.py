from concurrent.futures import ThreadPoolExecutor
import threading
from uuid import uuid4

import pytest

from app.db import pool
from app.modulos import pasarela_simulada
from app.modulos.pedidos import confirmar_compra, procesar_eventos_pendientes
from app.modulos.reservas import LineaSolicitud, crear_checkout, expirar_reserva
from escenarios import (
    crear_reserva_vencida,
    estados,
    movimientos_de,
    nuevo_cliente,
    preparar_escenario,
    reconstruir_saldos,
    saldos,
)


@pytest.fixture(autouse=True)
def restaurar_pasarela():
    pasarela_simulada.reiniciar_simulador()
    yield
    pasarela_simulada.reiniciar_simulador()


def test_caso_a_compromete_la_reserva_y_deja_movimientos_compromiso():
    darkstore_id, producto_id = preparar_escenario(10)
    cliente_id = nuevo_cliente()
    with pool.connection() as conn:
        res = crear_checkout(
            conn,
            cliente_id,
            darkstore_id,
            [LineaSolicitud(producto_id=producto_id, cantidad=3)],
            uuid4(),
        )
    pedido_id = res["pedido"]["pedido_id"]
    reserva_id = res["reserva"]["reserva_id"]
    intento_id = uuid4()

    with pool.connection() as conn:
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO intento_pago (intento_id, pedido_id, monto, estado, fecha_resultado)
                VALUES (%s, %s, 300.00, 'APROBADO', clock_timestamp())
                """,
                (intento_id, pedido_id),
            )
            confirmar_compra(conn, intento_id)

    assert estados(reserva_id) == ("COMPROMETIDA", "PAGO_CONFIRMADO", "CONFIRMADO")
    movs = movimientos_de(reserva_id)
    tipos_saldos = [(t, s, c) for t, s, c in movs if t == "COMPROMISO"]
    assert ("COMPROMISO", "R", -3) in tipos_saldos
    assert ("COMPROMISO", "C", 3) in tipos_saldos

    e_act, r_act, _ = saldos(darkstore_id, producto_id)
    e_rec, r_rec = reconstruir_saldos(darkstore_id, producto_id)
    assert (e_act, r_act) == (e_rec, r_rec)


def test_caso_b_pago_tardio_reembolsa_el_100_por_ciento_sin_revivir_la_reserva():
    darkstore_id, producto_id = preparar_escenario(5)
    cliente_id = nuevo_cliente()
    pedido_id, reserva_id = crear_reserva_vencida(cliente_id, darkstore_id, producto_id, cantidad=1)
    intento_id = uuid4()

    with pool.connection() as conn:
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO intento_pago (intento_id, pedido_id, monto, estado, fecha_resultado)
                VALUES (%s, %s, 100.00, 'APROBADO', clock_timestamp())
                """,
                (intento_id, pedido_id),
            )
            conn.execute(
                """
                INSERT INTO evento_interno (evento_id, tipo, productor, intento_id, procesado)
                VALUES (gen_random_uuid(), 'EV-01', 'M04', %s, false)
                """,
                (intento_id,),
            )

    procesar_eventos_pendientes()

    estado_res, motivo_cierre, estado_ped = estados(reserva_id)
    assert estado_res == "EXPIRADA"
    assert motivo_cierre == "VENCIMIENTO"
    assert estado_ped == "SIN_COMPRA"

    with pool.connection() as conn:
        fila_ped = conn.execute(
            "SELECT ajuste_pendiente, intento_aplicado_id FROM pedido WHERE pedido_id = %s",
            (pedido_id,),
        ).fetchone()
        fila_int = conn.execute(
            "SELECT estado FROM intento_pago WHERE intento_id = %s",
            (intento_id,),
        ).fetchone()
        fila_reem = conn.execute(
            "SELECT monto, motivo FROM reembolso WHERE intento_id = %s",
            (intento_id,),
        ).fetchone()

    assert fila_ped == (True, None)
    assert fila_int[0] != "RECHAZADO"
    assert fila_int[0] == "REEMBOLSADO_TOTAL"
    assert str(fila_reem[0]) == "100.00"
    assert fila_reem[1] == "PAGO_TARDIO"


def test_pago_tardio_con_la_reserva_ya_expirada_por_el_proceso():
    darkstore_id, producto_id = preparar_escenario(5)
    cliente_id = nuevo_cliente()
    pedido_id, reserva_id = crear_reserva_vencida(cliente_id, darkstore_id, producto_id, cantidad=1)

    with pool.connection() as conn:
        with conn.transaction():
            assert expirar_reserva(conn, reserva_id) is True

    intento_id = uuid4()
    with pool.connection() as conn:
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO intento_pago (intento_id, pedido_id, monto, estado, fecha_resultado)
                VALUES (%s, %s, 100.00, 'APROBADO', clock_timestamp())
                """,
                (intento_id, pedido_id),
            )
            conn.execute(
                """
                INSERT INTO evento_interno (evento_id, tipo, productor, intento_id, procesado)
                VALUES (gen_random_uuid(), 'EV-01', 'M04', %s, false)
                """,
                (intento_id,),
            )

    procesar_eventos_pendientes()

    assert estados(reserva_id)[0] == "EXPIRADA"
    assert estados(reserva_id)[2] == "SIN_COMPRA"
    with pool.connection() as conn:
        motivo = conn.execute(
            "SELECT motivo FROM reembolso WHERE intento_id = %s",
            (intento_id,),
        ).fetchone()[0]
    assert motivo == "PAGO_TARDIO"


def test_carrera_pago_contra_vencimiento_gana_una_sola_sentencia():
    darkstore_id, producto_id = preparar_escenario(5)
    cliente_id = nuevo_cliente()
    pedido_id, reserva_id = crear_reserva_vencida(cliente_id, darkstore_id, producto_id, cantidad=1)

    intento_id = uuid4()
    with pool.connection() as conn:
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO intento_pago (intento_id, pedido_id, monto, estado, fecha_resultado)
                VALUES (%s, %s, 100.00, 'APROBADO', clock_timestamp())
                """,
                (intento_id, pedido_id),
            )

    barrera = threading.Barrier(2)

    def _confirmar():
        barrera.wait()
        with pool.connection() as c:
            with c.transaction():
                confirmar_compra(c, intento_id)

    def _expirar():
        barrera.wait()
        with pool.connection() as c:
            with c.transaction():
                expirar_reserva(c, reserva_id)

    with ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(_confirmar)
        f2 = ex.submit(_expirar)
        f1.result()
        f2.result()

    estado_res, _, estado_ped = estados(reserva_id)
    assert not (estado_ped == "CONFIRMADO" and estado_res == "EXPIRADA")
    assert estado_res == "EXPIRADA"
    assert estado_ped == "SIN_COMPRA"


def test_cobro_duplicado_se_reembolsa_y_el_inventario_se_compromete_una_sola_vez():
    darkstore_id, producto_id = preparar_escenario(5)
    cliente_id = nuevo_cliente()
    with pool.connection() as conn:
        res = crear_checkout(
            conn,
            cliente_id,
            darkstore_id,
            [LineaSolicitud(producto_id=producto_id, cantidad=2)],
            uuid4(),
        )
    pedido_id = res["pedido"]["pedido_id"]
    reserva_id = res["reserva"]["reserva_id"]

    intento1 = uuid4()
    intento2 = uuid4()

    with pool.connection() as conn:
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO intento_pago (intento_id, pedido_id, monto, estado, fecha_resultado)
                VALUES
                    (%s, %s, 200.00, 'APROBADO', clock_timestamp()),
                    (%s, %s, 200.00, 'APROBADO', clock_timestamp())
                """,
                (intento1, pedido_id, intento2, pedido_id),
            )
            confirmar_compra(conn, intento1)
            reem_id = confirmar_compra(conn, intento2)

    assert reem_id is not None
    assert estados(reserva_id) == ("COMPROMETIDA", "PAGO_CONFIRMADO", "CONFIRMADO")

    e, r, d = saldos(darkstore_id, producto_id)
    assert (e, r, d) == (5, 0, 3)

    with pool.connection() as conn:
        motivo = conn.execute(
            "SELECT motivo FROM reembolso WHERE intento_id = %s",
            (intento2,),
        ).fetchone()[0]
    assert motivo == "COBRO_DUPLICADO"


def test_evento_repetido_no_duplica_el_efecto():
    darkstore_id, producto_id = preparar_escenario(5)
    cliente_id = nuevo_cliente()
    with pool.connection() as conn:
        res = crear_checkout(
            conn,
            cliente_id,
            darkstore_id,
            [LineaSolicitud(producto_id=producto_id, cantidad=1)],
            uuid4(),
        )
    pedido_id = res["pedido"]["pedido_id"]
    intento_id = uuid4()

    with pool.connection() as conn:
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO intento_pago (intento_id, pedido_id, monto, estado, fecha_resultado)
                VALUES (%s, %s, 100.00, 'APROBADO', clock_timestamp())
                """,
                (intento_id, pedido_id),
            )
            conn.execute(
                """
                INSERT INTO evento_interno (evento_id, tipo, productor, intento_id, procesado)
                VALUES
                    (gen_random_uuid(), 'EV-01', 'M04', %s, false),
                    (gen_random_uuid(), 'EV-01', 'M04', %s, false)
                """,
                (intento_id, intento_id),
            )

    procesar_eventos_pendientes()

    with pool.connection() as conn:
        total_lineas = conn.execute(
            "SELECT COUNT(*) FROM linea_pedido WHERE pedido_id = %s",
            (pedido_id,),
        ).fetchone()[0]
    assert total_lineas == 1