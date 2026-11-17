from uuid import uuid4
from fastapi.testclient import TestClient

from app.db import pool
from app.main import app
from app.modulos import pasarela_simulada
from app.modulos.pagos import recuperar_intentos_iniciados
from app.modulos.pedidos import procesar_eventos_pendientes
from app.modulos.reservas import LineaSolicitud, crear_checkout
from escenarios import estados, nuevo_cliente, preparar_escenario

client = TestClient(app)


def test_un_intento_iniciado_viejo_pasa_a_en_verificacion_sin_volver_a_cobrar():
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
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO intento_pago (intento_id, pedido_id, monto, fecha_inicio, estado)
                VALUES (%s, %s, 100.00, clock_timestamp() - interval '45 seconds', 'INICIADO')
                """,
                (intento_id, pedido_id),
            )

    recuperados = recuperar_intentos_iniciados(segundos=30)
    assert recuperados >= 1

    with pool.connection() as conn:
        estado = conn.execute(
            "SELECT estado FROM intento_pago WHERE intento_id = %s",
            (intento_id,),
        ).fetchone()[0]
    assert estado == "EN_VERIFICACION"


def test_un_evento_sin_procesar_se_reprocesa_una_sola_vez():
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
        intento_id = uuid4()
        evento_id = uuid4()
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO intento_pago (intento_id, pedido_id, monto, estado, fecha_resultado)
                VALUES (%s, %s, 200.00, 'APROBADO', clock_timestamp())
                """,
                (intento_id, pedido_id),
            )
            conn.execute(
                """
                INSERT INTO evento_interno (evento_id, tipo, productor, intento_id, procesado)
                VALUES (%s, 'EV-01', 'M04', %s, false)
                """,
                (evento_id, intento_id),
            )

    procesar_eventos_pendientes()
    procesar_eventos_pendientes()

    assert estados(reserva_id) == ("COMPROMETIDA", "PAGO_CONFIRMADO", "CONFIRMADO")

    with pool.connection() as conn:
        fila_ev = conn.execute(
            "SELECT procesado, intentos_procesamiento FROM evento_interno WHERE evento_id = %s",
            (evento_id,),
        ).fetchone()
        conteo_movs = conn.execute(
            "SELECT COUNT(*) FROM movimiento_inventario WHERE referencia_reserva_id = %s AND tipo = 'COMPROMISO'",
            (reserva_id,),
        ).fetchone()[0]

    assert fila_ev == (True, 1)
    assert conteo_movs == 1


def test_repetir_la_peticion_por_timeout_no_duplica_el_cobro():
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

    pasarela_simulada._estado_simulador["cobro"] = "NO_RESPONDER"
    clave = str(uuid4())

    r1 = client.post(
        f"/v1/pedidos/{pedido_id}/intentos-pago",
        headers={"Clave-Operacion": clave, "Cliente-Id": str(cliente_id)},
        json={"monto": "100.00"},
    )
    assert r1.status_code == 202

    r2 = client.post(
        f"/v1/pedidos/{pedido_id}/intentos-pago",
        headers={"Clave-Operacion": clave, "Cliente-Id": str(cliente_id)},
        json={"monto": "100.00"},
    )
    assert r2.status_code == 202
    assert r2.headers.get("Resultado-Previo") == "true"

    with pool.connection() as conn:
        total_intentos = conn.execute(
            "SELECT COUNT(*) FROM intento_pago WHERE pedido_id = %s",
            (pedido_id,),
        ).fetchone()[0]
    assert total_intentos == 1