import time
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app.db import pool
from app.errores import ErrorDeNegocio
from app.main import app
from app.modulos import pasarela_simulada
from app.modulos.pedidos import confirmar_compra
from app.modulos.reservas import exigir_reserva_vigente, expirar_reserva
from escenarios import estados, movimientos_de, nuevo_cliente, preparar_escenario, saldos

client = TestClient(app)
HOLGURA = 0.8


@pytest.fixture(autouse=True)
def restaurar_pasarela():
    pasarela_simulada.reiniciar_simulador()
    yield
    pasarela_simulada.reiniciar_simulador()


def reserva_con_antiguedad(segundos: int):
    darkstore_id, producto_id = preparar_escenario(1)
    cliente_id = nuevo_cliente()
    pedido_id, reserva_id = uuid4(), uuid4()
    with pool.connection() as conn:
        creada = conn.execute("SELECT clock_timestamp() - make_interval(secs => %s)", (segundos,)).fetchone()[0]
        conn.execute("SET LOCAL session_replication_role = replica")
        conn.execute(
            "INSERT INTO pedido (pedido_id, reserva_id, cliente_id, darkstore_id, total_vigente) VALUES (%s, %s, %s, %s, 100.00)",
            (pedido_id, reserva_id, cliente_id, darkstore_id),
        )
        conn.execute(
            "INSERT INTO reserva (reserva_id, pedido_id, cliente_id, darkstore_id, t_creacion, t_vence) "
            "VALUES (%s, %s, %s, %s, %s, %s + INTERVAL '300 seconds')",
            (reserva_id, pedido_id, cliente_id, darkstore_id, creada, creada),
        )
        conn.execute(
            "INSERT INTO linea_reserva (reserva_id, producto_id, darkstore_id, cantidad, precio_unitario) VALUES (%s, %s, %s, 1, 100.00)",
            (reserva_id, producto_id, darkstore_id),
        )
    with pool.connection() as conn:
        movimiento_id = conn.execute(
            "INSERT INTO movimiento_inventario (darkstore_id, producto_id, tipo, motivo, clave_operacion, referencia_reserva_id) "
            "VALUES (%s, %s, 'RESERVA', 'Reserva de checkout', %s, %s) RETURNING movimiento_id",
            (darkstore_id, producto_id, uuid4(), reserva_id),
        ).fetchone()[0]
        conn.execute("INSERT INTO movimiento_saldo (movimiento_id, saldo, cantidad) VALUES (%s, 'R', 1)", (movimiento_id,))
    return {
        "inicio": time.monotonic(),
        "darkstore_id": darkstore_id,
        "producto_id": producto_id,
        "cliente_id": cliente_id,
        "pedido_id": pedido_id,
        "reserva_id": reserva_id,
    }


def pagar(r):
    return client.post(
        f"/v1/pedidos/{r['pedido_id']}/intentos-pago",
        headers={"Clave-Operacion": str(uuid4()), "Cliente-Id": str(r["cliente_id"])},
        json={"monto": "100.00"},
    )


def aprobacion_directa(r):
    intento_id = uuid4()
    with pool.connection() as conn:
        with conn.transaction():
            conn.execute(
                "INSERT INTO intento_pago (intento_id, pedido_id, monto, estado, fecha_resultado) "
                "VALUES (%s, %s, 100.00, 'APROBADO', clock_timestamp())",
                (intento_id, r["pedido_id"]),
            )
            confirmar_compra(conn, intento_id)
    return intento_id


def intentos_de(pedido_id):
    with pool.connection() as conn:
        return conn.execute("SELECT estado FROM intento_pago WHERE pedido_id = %s", (pedido_id,)).fetchall()


def dentro_del_segundo(r):
    transcurrido = time.monotonic() - r["inicio"]
    assert transcurrido < HOLGURA, f"La prueba tardó {transcurrido:.2f} s: el límite de 299 s ya no es exacto"


def test_at02_a_los_299_s_la_reserva_sigue_vigente():
    r = reserva_con_antiguedad(299)
    with pool.connection() as conn:
        exigir_reserva_vigente(conn, r["reserva_id"])
        assert expirar_reserva(conn, r["reserva_id"]) is False
    dentro_del_segundo(r)
    assert estados(r["reserva_id"])[0] == "ACTIVA"
    assert saldos(r["darkstore_id"], r["producto_id"]) == (1, 1, 0)


def test_at02_a_los_299_s_un_pago_aprobado_compromete_la_reserva():
    r = reserva_con_antiguedad(299)
    aprobacion_directa(r)
    dentro_del_segundo(r)
    assert estados(r["reserva_id"]) == ("COMPROMETIDA", "PAGO_CONFIRMADO", "CONFIRMADO")
    assert ("COMPROMISO", "C", 1) in movimientos_de(r["reserva_id"])


def test_at02_a_los_300_s_la_reserva_ya_vencio():
    r = reserva_con_antiguedad(300)
    with pool.connection() as conn:
        with pytest.raises(ErrorDeNegocio) as error:
            exigir_reserva_vigente(conn, r["reserva_id"])
    assert error.value.codigo == "RESERVA_VENCIDA"
    with pool.connection() as conn:
        assert expirar_reserva(conn, r["reserva_id"]) is True
    assert estados(r["reserva_id"]) == ("EXPIRADA", "VENCIMIENTO", "SIN_COMPRA")
    assert saldos(r["darkstore_id"], r["producto_id"]) == (1, 0, 1)


def test_at02_a_los_300_s_el_pago_se_rechaza_sin_cobrar():
    r = reserva_con_antiguedad(300)
    respuesta = pagar(r)
    assert respuesta.status_code == 409
    assert respuesta.json()["codigo"] == "RESERVA_VENCIDA"
    assert intentos_de(r["pedido_id"]) == []


def test_at02_una_aprobacion_a_los_300_s_no_compromete_y_se_reembolsa():
    r = reserva_con_antiguedad(300)
    intento_id = aprobacion_directa(r)
    assert estados(r["reserva_id"]) == ("EXPIRADA", "VENCIMIENTO", "SIN_COMPRA")
    assert all(tipo != "COMPROMISO" for tipo, _, _ in movimientos_de(r["reserva_id"]))
    assert saldos(r["darkstore_id"], r["producto_id"]) == (1, 0, 1)
    with pool.connection() as conn:
        motivos = conn.execute("SELECT motivo FROM reembolso WHERE intento_id = %s", (intento_id,)).fetchall()
    assert motivos == [("PAGO_TARDIO",)]
