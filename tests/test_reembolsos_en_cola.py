from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from psycopg.errors import DeadlockDetected

from app.db import pool
from app.main import app
from app.modulos import pasarela_simulada
from app.modulos.pagos import _ejecutar_con_reintento_concurrencia, enviar_reembolso, solicitar_reembolso
from test_c2_reembolsos import USUARIO_RESPONSABLE, _preparar_intento_aprobado

client = TestClient(app)


@pytest.fixture(autouse=True)
def restaurar_pasarela():
    pasarela_simulada.reiniciar_simulador()
    yield
    pasarela_simulada.reiniciar_simulador()


def dos_reembolsos_en_cola():
    pedido_id, intento_id = _preparar_intento_aprobado(Decimal("200.00"))
    pasarela_simulada._estado_simulador["reembolso"] = "NO_RESPONDER"
    with pool.connection() as conn, conn.transaction():
        reserva_id, producto_id = conn.execute(
            "SELECT lr.reserva_id, lr.producto_id FROM linea_reserva lr JOIN pedido p USING (reserva_id) WHERE p.pedido_id = %s",
            (pedido_id,)).fetchone()
        primero = solicitar_reembolso(conn, intento_id, Decimal("50.00"), "PRUEBA_1")
        segundo = solicitar_reembolso(conn, intento_id, Decimal("30.00"), "PRUEBA_2",
                                      reserva_origen_id=reserva_id, producto_origen_id=producto_id)
    assert segundo is None
    enviar_reembolso(primero)
    pasarela_simulada._estado_simulador["reembolso"] = "CONFIRMAR"
    return intento_id, primero


def estado(intento_id):
    with pool.connection() as conn:
        estado, = conn.execute("SELECT estado FROM intento_pago WHERE intento_id = %s", (intento_id,)).fetchone()
        confirmados = conn.execute("SELECT count(*) FROM reembolso WHERE intento_id = %s AND fecha_confirmacion IS NOT NULL",
                                   (intento_id,)).fetchone()[0]
    return estado, confirmados


def test_la_notificacion_de_un_reembolso_envia_el_siguiente_en_cola():
    intento_id, primero = dos_reembolsos_en_cola()

    r = client.post("/v1/pasarela/notificaciones", json={
        "objeto": "REEMBOLSO", "objeto_id": str(primero), "referencia_externa": "PSP-R1", "resultado": "CONFIRMADO"})

    assert r.status_code == 200, r.text
    assert estado(intento_id) == ("REEMBOLSADO_PARCIAL", 2)


def test_la_verificacion_manual_de_un_reembolso_envia_el_siguiente_en_cola():
    intento_id, primero = dos_reembolsos_en_cola()

    r = client.post(f"/v1/reembolsos/{primero}/verificacion",
                    headers={"Clave-Operacion": str(uuid4()), "Usuario-Id": USUARIO_RESPONSABLE},
                    json={"resultado": "CONFIRMADO", "evidencia": {"soporte": "estado de cuenta"}})

    assert r.status_code == 200, r.text
    assert estado(intento_id) == ("REEMBOLSADO_PARCIAL", 2)


def test_notificar_un_reembolso_inexistente_da_404():
    r = client.post("/v1/pasarela/notificaciones", json={
        "objeto": "REEMBOLSO", "objeto_id": str(uuid4()), "referencia_externa": "PSP-X", "resultado": "CONFIRMADO"})
    assert r.status_code == 404


@pytest.mark.parametrize("cuerpo", [
    {"objeto": "INTENTO", "resultado": "CONFIRMADO", "referencia_externa": "PSP-1"},
    {"objeto": "REEMBOLSO", "resultado": "APROBADO", "referencia_externa": "PSP-1"},
    {"objeto": "INTENTO", "resultado": "APROBADO", "referencia_externa": "   "},
])
def test_notificaciones_incongruentes_dan_400(cuerpo):
    r = client.post("/v1/pasarela/notificaciones", json={"objeto_id": str(uuid4()), **cuerpo})
    assert r.status_code == 400


def test_verificar_un_reembolso_inexistente_o_fuera_de_verificacion():
    _, intento_id = _preparar_intento_aprobado(Decimal("200.00"))
    with pool.connection() as conn, conn.transaction():
        reembolso = solicitar_reembolso(conn, intento_id, Decimal("20.00"), "PRUEBA")
    cabeceras = {"Usuario-Id": USUARIO_RESPONSABLE}
    cuerpo = {"resultado": "CONFIRMADO", "evidencia": {"x": 1}}
    falta = client.post(f"/v1/reembolsos/{uuid4()}/verificacion", headers={**cabeceras, "Clave-Operacion": str(uuid4())}, json=cuerpo)
    fuera = client.post(f"/v1/reembolsos/{reembolso}/verificacion", headers={**cabeceras, "Clave-Operacion": str(uuid4())}, json=cuerpo)
    assert falta.status_code == 404
    assert fuera.status_code == 409 and fuera.json()["codigo"] == "ESTADO_INCOMPATIBLE"


def test_un_interbloqueo_se_reintenta_y_al_tercero_se_rinde():
    llamadas = []

    def falla_dos_veces():
        llamadas.append(1)
        if len(llamadas) < 3:
            raise DeadlockDetected()
        return "ok"

    def falla_siempre():
        raise DeadlockDetected()

    assert _ejecutar_con_reintento_concurrencia(falla_dos_veces) == "ok"
    assert len(llamadas) == 3
    with pytest.raises(DeadlockDetected):
        _ejecutar_con_reintento_concurrencia(falla_siempre)
