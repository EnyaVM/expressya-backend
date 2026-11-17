from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app.db import pool
from app.main import app
from app.modulos import pasarela_simulada
from app.modulos.pagos import enviar_reembolso, solicitar_reembolso
from app.modulos.reservas import LineaSolicitud, crear_checkout
from escenarios import nuevo_cliente, preparar_escenario

client = TestClient(app)
USUARIO_RESPONSABLE = "77777777-7777-7777-7777-777777777777"


@pytest.fixture(autouse=True)
def restaurar_pasarela():
    pasarela_simulada.reiniciar_simulador()
    yield
    pasarela_simulada.reiniciar_simulador()


def _preparar_intento_aprobado(monto: Decimal = Decimal("200.00")):
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
        intento_id = uuid4()
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO intento_pago (intento_id, pedido_id, monto, estado, fecha_resultado)
                VALUES (%s, %s, %s, 'APROBADO', clock_timestamp())
                """,
                (intento_id, pedido_id, monto),
            )
    return pedido_id, intento_id


def test_reembolso_confirmado_deja_el_intento_reembolsado_total():
    _, intento_id = _preparar_intento_aprobado(Decimal("200.00"))

    with pool.connection() as conn:
        with conn.transaction():
            reembolso_id = solicitar_reembolso(conn, intento_id, Decimal("200.00"), "PAGO_TARDIO")

    assert reembolso_id is not None
    enviar_reembolso(reembolso_id)

    with pool.connection() as conn:
        estado_int, reem_actual = conn.execute(
            "SELECT estado, reembolso_actual_id FROM intento_pago WHERE intento_id = %s",
            (intento_id,),
        ).fetchone()
    assert estado_int == "REEMBOLSADO_TOTAL"
    assert reem_actual is None


def test_reembolso_sin_respuesta_queda_en_verificacion_sin_reenviar_y_el_segundo_espera():
    pedido_id, intento_id = _preparar_intento_aprobado(Decimal("200.00"))
    pasarela_simulada._estado_simulador["reembolso"] = "NO_RESPONDER"

    with pool.connection() as conn:
        with conn.transaction():
            reserva_id = conn.execute(
                "SELECT reserva_id FROM pedido WHERE pedido_id = %s",
                (pedido_id,),
            ).fetchone()[0]
            producto_id = conn.execute(
                "SELECT producto_id FROM linea_reserva WHERE reserva_id = %s LIMIT 1",
                (reserva_id,),
            ).fetchone()[0]

            reem1 = solicitar_reembolso(conn, intento_id, Decimal("100.00"), "CANCELACION_1")
            reem2 = solicitar_reembolso(
                conn,
                intento_id,
                Decimal("100.00"),
                "CANCELACION_2",
                reserva_origen_id=reserva_id,
                producto_origen_id=producto_id,
            )

    assert reem1 is not None
    assert reem2 is None  # El segundo queda en cola esperando

    enviar_reembolso(reem1)

    with pool.connection() as conn:
        estado_int, reem_actual = conn.execute(
            "SELECT estado, reembolso_actual_id FROM intento_pago WHERE intento_id = %s",
            (intento_id,),
        ).fetchone()
        filas_reem = conn.execute(
            "SELECT reembolso_id, fecha_envio FROM reembolso WHERE intento_id = %s ORDER BY fecha_solicitud ASC",
            (intento_id,),
        ).fetchall()

    assert estado_int == "REEMBOLSO_EN_VERIFICACION"
    assert str(reem_actual) == str(reem1)
    assert filas_reem[0][1] is not None
    assert filas_reem[1][1] is None


def test_cl06_resuelve_un_cobro_en_verificacion_con_evidencia():
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

    resp_pago = client.post(
        f"/v1/pedidos/{pedido_id}/intentos-pago",
        headers={"Clave-Operacion": str(uuid4()), "Cliente-Id": str(cliente_id)},
        json={"monto": "100.00"},
    )
    assert resp_pago.status_code == 202
    intento_id = resp_pago.json()["intento_id"]

    resp_verif = client.post(
        f"/v1/intentos-pago/{intento_id}/verificacion",
        headers={
            "Clave-Operacion": str(uuid4()),
            "Usuario-Id": USUARIO_RESPONSABLE,
        },
        json={
            "resultado": "APROBADO",
            "evidencia_fuente": "PORTAL_BANCO",
            "evidencia": {"folio": "AUTH-88412"},
        },
    )
    assert resp_verif.status_code == 200
    assert resp_verif.json()["estado"] == "APROBADO"


def test_cl06_rechaza_si_no_esta_en_verificacion():
    _, intento_id = _preparar_intento_aprobado(Decimal("200.00"))

    resp = client.post(
        f"/v1/intentos-pago/{intento_id}/verificacion",
        headers={
            "Clave-Operacion": str(uuid4()),
            "Usuario-Id": USUARIO_RESPONSABLE,
        },
        json={
            "resultado": "APROBADO",
            "evidencia_fuente": "PORTAL_BANCO",
            "evidencia": {"folio": "AUTH-001"},
        },
    )
    assert resp.status_code == 409
    assert resp.json()["codigo"] == "ESTADO_INCOMPATIBLE"


def test_cl06_sin_rol_responsable_da_403():
    _, intento_id = _preparar_intento_aprobado(Decimal("200.00"))
    usuario_sin_permiso = str(uuid4())
    
    # Insertamos el usuario en la BD para cumplir con la FK de registro_operacion
    with pool.connection() as conn:
        with conn.transaction():
            conn.execute(
                "INSERT INTO usuario_operativo (usuario_id, nombre) VALUES (%s, %s)",
                (usuario_sin_permiso, "Usuario sin rol")
            )

    resp = client.post(
        f"/v1/intentos-pago/{intento_id}/verificacion",
        headers={
            "Clave-Operacion": str(uuid4()),
            "Usuario-Id": usuario_sin_permiso,
        },
        json={
            "resultado": "APROBADO",
            "evidencia_fuente": "PORTAL_BANCO",
            "evidencia": {"folio": "AUTH-002"},
        },
    )
    assert resp.status_code == 403
    assert resp.json()["codigo"] == "SIN_PERMISO"


def test_cl06_resuelve_un_reembolso_en_verificacion():
    _, intento_id = _preparar_intento_aprobado(Decimal("200.00"))
    pasarela_simulada._estado_simulador["reembolso"] = "NO_RESPONDER"

    with pool.connection() as conn:
        with conn.transaction():
            reem_id = solicitar_reembolso(conn, intento_id, Decimal("200.00"), "PAGO_TARDIO")
    enviar_reembolso(reem_id)

    resp = client.post(
        f"/v1/reembolsos/{reem_id}/verificacion",
        headers={
            "Clave-Operacion": str(uuid4()),
            "Usuario-Id": USUARIO_RESPONSABLE,
        },
        json={
            "resultado": "CONFIRMADO",
            "evidencia": {"soporte": "manual"},
        },
    )
    assert resp.status_code == 200
    assert resp.json()["estado"] == "REEMBOLSADO_TOTAL"


def test_cl17_solo_reintenta_desde_reembolso_fallido():
    _, intento_id = _preparar_intento_aprobado(Decimal("200.00"))
    pasarela_simulada._estado_simulador["reembolso"] = "NO_RESPONDER"

    with pool.connection() as conn:
        with conn.transaction():
            reem_id = solicitar_reembolso(conn, intento_id, Decimal("200.00"), "PAGO_TARDIO")
    enviar_reembolso(reem_id)

    # Desde REEMBOLSO_EN_VERIFICACION da 409 ESTADO_INCOMPATIBLE
    resp_bad = client.post(
        f"/v1/reembolsos/{reem_id}/reintentos",
        headers={
            "Clave-Operacion": str(uuid4()),
            "Usuario-Id": USUARIO_RESPONSABLE,
        },
    )
    assert resp_bad.status_code == 409
    assert resp_bad.json()["codigo"] == "ESTADO_INCOMPATIBLE"

    # Forzar a FALLIDO con CL-06
    client.post(
        f"/v1/reembolsos/{reem_id}/verificacion",
        headers={
            "Clave-Operacion": str(uuid4()),
            "Usuario-Id": USUARIO_RESPONSABLE,
        },
        json={
            "resultado": "FALLIDO",
            "evidencia": {"error": "timeout"},
        },
    )

    # Desde REEMBOLSO_FALLIDO responde 202
    pasarela_simulada._estado_simulador["reembolso"] = "CONFIRMAR"
    resp_ok = client.post(
        f"/v1/reembolsos/{reem_id}/reintentos",
        headers={
            "Clave-Operacion": str(uuid4()),
            "Usuario-Id": USUARIO_RESPONSABLE,
        },
    )
    assert resp_ok.status_code == 202


def test_el_reembolso_nunca_supera_lo_cobrado():
    _, intento_id = _preparar_intento_aprobado(Decimal("100.00"))

    with pytest.raises(Exception):
        with pool.connection() as conn:
            with conn.transaction():
                solicitar_reembolso(conn, intento_id, Decimal("150.00"), "EXCESO")