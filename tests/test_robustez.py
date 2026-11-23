from concurrent.futures import ThreadPoolExecutor
import threading
import time
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest

from app.db import pool
from app.main import app
from app.modulos import pagos, pasarela_simulada, pedidos
from escenarios import nuevo_cliente
from escenarios_c3 import (
    cancelar,
    estado_intento,
    estado_pedido,
    pedido_pagado,
    reembolsos_de,
    tienda_con_productos,
)

cliente_sin_excepciones = TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def restaurar_pasarela():
    pasarela_simulada.reiniciar_simulador()
    yield
    pasarela_simulada.reiniciar_simulador()


def _pedido_pendiente(darkstore_id: UUID, producto_id: UUID, cantidad: int = 1):
    cliente_id = nuevo_cliente()
    respuesta = cliente_sin_excepciones.post(
        "/v1/checkouts",
        headers={"Clave-Operacion": str(uuid4()), "Cliente-Id": str(cliente_id)},
        json={"darkstore_id": str(darkstore_id), "lineas": [{"producto_id": str(producto_id), "cantidad": cantidad}]},
    )
    assert respuesta.status_code == 201, respuesta.text
    pedido = respuesta.json()["pedido"]
    return UUID(pedido["pedido_id"]), cliente_id, f"{float(pedido['total_vigente']):.2f}"


def _pagar(pedido_id, cliente_id, monto, clave):
    return cliente_sin_excepciones.post(
        f"/v1/pedidos/{pedido_id}/intentos-pago",
        headers={"Clave-Operacion": str(clave), "Cliente-Id": str(cliente_id)},
        json={"monto": monto},
    )


def _intento_aprobado_con_evento(pedido_id: UUID, monto: str) -> tuple[UUID, UUID]:
    intento_id = uuid4()
    with pool.connection() as conn:
        with conn.transaction():
            conn.execute(
                "INSERT INTO intento_pago (intento_id, pedido_id, monto, estado, fecha_resultado) "
                "VALUES (%s, %s, %s, 'APROBADO', clock_timestamp())",
                (intento_id, pedido_id, monto),
            )
            evento_id = conn.execute(
                "INSERT INTO evento_interno (tipo, productor, intento_id) VALUES ('EV-01', 'M04', %s) RETURNING evento_id",
                (intento_id,),
            ).fetchone()[0]
    return intento_id, evento_id


def _evento(evento_id: UUID):
    with pool.connection() as conn:
        return conn.execute(
            "SELECT procesado, intentos_procesamiento FROM evento_interno WHERE evento_id = %s",
            (evento_id,),
        ).fetchone()


def test_cobro_duplicado_sobre_pedido_cancelado_se_reembolsa():
    darkstore_id, (producto_id,) = tienda_con_productos(1)
    pedido_id, cliente_id, _ = pedido_pagado(darkstore_id, [(producto_id, 1)])
    assert cancelar(pedido_id, cliente_id).status_code == 200

    intento_tardio, evento_id = _intento_aprobado_con_evento(pedido_id, "100.00")
    pedidos.procesar_eventos_pendientes()

    assert _evento(evento_id)[0] is True
    assert estado_pedido(pedido_id) == "CANCELADO"
    assert [r[2] for r in reembolsos_de(intento_tardio)] == ["COBRO_DUPLICADO"]
    assert estado_intento(intento_tardio) == "REEMBOLSADO_TOTAL"


def test_un_evento_que_falla_no_bloquea_a_los_demas(monkeypatch):
    darkstore_id, productos = tienda_con_productos(2)
    pedido_malo, _, monto_malo = _pedido_pendiente(darkstore_id, productos[0])
    pedido_bueno, _, monto_bueno = _pedido_pendiente(darkstore_id, productos[1])
    intento_malo, evento_malo = _intento_aprobado_con_evento(pedido_malo, monto_malo)
    _, evento_bueno = _intento_aprobado_con_evento(pedido_bueno, monto_bueno)

    original = pedidos.confirmar_compra

    def confirmar_con_falla(conn, intento_id):
        if intento_id == intento_malo:
            raise RuntimeError("falla provocada")
        return original(conn, intento_id)

    monkeypatch.setattr(pedidos, "confirmar_compra", confirmar_con_falla)

    pedidos.procesar_eventos_pendientes()
    assert estado_pedido(pedido_bueno) == "CONFIRMADO"
    assert _evento(evento_malo) == (False, 1)

    for _ in range(10):
        pedidos.procesar_eventos_pendientes()
    assert _evento(evento_malo) == (False, pedidos.MAXIMO_INTENTOS_EVENTO)
    with pool.connection() as conn:
        avisos = conn.execute(
            "SELECT destinatario_tipo, tipo FROM aviso WHERE origen_id = %s",
            (evento_malo,),
        ).fetchall()
    assert avisos == [("RESPONSABLE", "EVENTO_SIN_PROCESAR")]


def test_caida_antes_de_enviar_el_reembolso_se_recupera(monkeypatch):
    darkstore_id, (producto_id,) = tienda_con_productos(1)
    pedido_id, cliente_id, intento_id = pedido_pagado(darkstore_id, [(producto_id, 2)])
    clave = uuid4()

    def caida(_reembolso_id):
        raise RuntimeError("caida provocada antes de llamar a la pasarela")

    monkeypatch.setattr(pagos, "enviar_reembolso", caida)
    primera = cliente_sin_excepciones.post(
        f"/v1/pedidos/{pedido_id}/cancelacion",
        headers={"Clave-Operacion": str(clave), "Cliente-Id": str(cliente_id)},
        json={"motivo": "caida"},
    )
    monkeypatch.undo()

    assert primera.status_code == 500
    assert estado_pedido(pedido_id) == "CANCELADO"
    assert reembolsos_de(intento_id)[0][4] is None
    assert estado_intento(intento_id) == "REEMBOLSO_EN_PROCESO"

    segunda = cancelar(pedido_id, cliente_id, clave, "caida")
    assert segunda.status_code == 200
    assert segunda.headers.get("Resultado-Previo") == "true"

    assert pagos.recuperar_reembolsos_sin_enviar(segundos=0) >= 1
    assert estado_intento(intento_id) == "REEMBOLSADO_TOTAL"
    assert len(reembolsos_de(intento_id)) == 1
    assert pagos.recuperar_reembolsos_sin_enviar(segundos=0) == 0 or estado_intento(intento_id) == "REEMBOLSADO_TOTAL"


def test_la_recuperacion_respeta_el_plazo_y_no_reenvia():
    darkstore_id, (producto_id,) = tienda_con_productos(1)
    pedido_id, cliente_id, intento_id = pedido_pagado(darkstore_id, [(producto_id, 1)])
    assert cancelar(pedido_id, cliente_id).status_code == 200
    enviado = reembolsos_de(intento_id)[0]

    pagos.recuperar_reembolsos_sin_enviar(segundos=0)

    assert reembolsos_de(intento_id) == [enviado]


def _contar_cobros(monkeypatch):
    llamadas = []
    candado = threading.Lock()
    original = pasarela_simulada.cobrar

    def cobrar_lento(intento_id, monto):
        with candado:
            llamadas.append(intento_id)
        time.sleep(0.3)
        return original(intento_id, monto)

    monkeypatch.setattr(pasarela_simulada, "cobrar", cobrar_lento)
    return llamadas


def test_misma_clave_en_dos_pedidos_simultaneos_cobra_una_sola_vez(monkeypatch):
    darkstore_id, productos = tienda_con_productos(2)
    pedido_a, cliente_a, monto_a = _pedido_pendiente(darkstore_id, productos[0])
    pedido_b, cliente_b, monto_b = _pedido_pendiente(darkstore_id, productos[1])
    llamadas = _contar_cobros(monkeypatch)
    clave = uuid4()

    with ThreadPoolExecutor(max_workers=2) as hilos:
        respuestas = list(
            hilos.map(
                lambda args: _pagar(*args, clave),
                [(pedido_a, cliente_a, monto_a), (pedido_b, cliente_b, monto_b)],
            )
        )

    assert sorted(r.status_code for r in respuestas) == [201, 409]
    assert len(llamadas) == 1
    conflicto = next(r for r in respuestas if r.status_code == 409)
    assert conflicto.json()["codigo"] == "CONFLICTO_DE_CLAVE"


def test_misma_clave_mismo_pedido_simultaneo_devuelve_el_mismo_resultado(monkeypatch):
    darkstore_id, (producto_id,) = tienda_con_productos(1)
    pedido_id, cliente_id, monto = _pedido_pendiente(darkstore_id, producto_id)
    llamadas = _contar_cobros(monkeypatch)
    clave = uuid4()

    with ThreadPoolExecutor(max_workers=2) as hilos:
        respuestas = list(hilos.map(lambda _: _pagar(pedido_id, cliente_id, monto, clave), range(2)))

    assert [r.status_code for r in respuestas] == [201, 201]
    assert respuestas[0].json() == respuestas[1].json()
    assert len(llamadas) == 1


def test_otro_cliente_no_recibe_la_respuesta_guardada_de_un_pago():
    darkstore_id, (producto_id,) = tienda_con_productos(1)
    pedido_id, cliente_id, monto = _pedido_pendiente(darkstore_id, producto_id)
    clave = uuid4()
    assert _pagar(pedido_id, cliente_id, monto, clave).status_code == 201

    intruso = _pagar(pedido_id, nuevo_cliente(), monto, clave)

    assert intruso.status_code == 409
    assert intruso.json()["codigo"] == "CONFLICTO_DE_CLAVE"
    assert "intento_id" not in intruso.text


@pytest.mark.parametrize("monto", ["NaN", "Infinity", "-Infinity", "1e9999", "sNaN", "-5.00", "0", "1.001", "abc", "1000000000000.00"])
def test_montos_invalidos_responden_400(monto):
    darkstore_id, (producto_id,) = tienda_con_productos(1)
    pedido_id, cliente_id, _ = _pedido_pendiente(darkstore_id, producto_id)

    respuesta = _pagar(pedido_id, cliente_id, monto, uuid4())

    assert respuesta.status_code == 400, respuesta.text
    assert respuesta.json()["codigo"] == "SOLICITUD_INVALIDA"


def test_reembolso_enviado_sin_respuesta_por_caida_pasa_a_verificacion_sin_reenviar(monkeypatch):
    darkstore_id, (producto_id,) = tienda_con_productos(1)
    pedido_id, cliente_id, intento_id = pedido_pagado(darkstore_id, [(producto_id, 1)])
    llamadas = []

    def caida_en_la_pasarela(reembolso_id, monto):
        llamadas.append(reembolso_id)
        raise RuntimeError("caida provocada al llamar a la pasarela")

    monkeypatch.setattr(pasarela_simulada, "reembolsar", caida_en_la_pasarela)
    assert cliente_sin_excepciones.post(
        f"/v1/pedidos/{pedido_id}/cancelacion",
        headers={"Clave-Operacion": str(uuid4()), "Cliente-Id": str(cliente_id)},
        json={"motivo": "caida"},
    ).status_code == 500

    pagos.recuperar_reembolsos_sin_enviar(segundos=0)

    assert estado_intento(intento_id) == "REEMBOLSO_EN_VERIFICACION"
    assert len(llamadas) == 1
