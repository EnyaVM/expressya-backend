from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient

from app.db import pool
from app.idempotencia import calcular_huella
from app.main import app
from app.modulos import pedidos
from escenarios import nuevo_cliente, preparar_escenario
from escenarios_c3 import pedido_pagado, reembolsos_de, tienda_con_productos
from escenarios_danos import danar, inspeccionar
from escenarios_faltantes import inventario, nuevo_usuario, reservar

client = TestClient(app)


def test_la_huella_no_depende_del_orden_de_los_campos():
    assert calcular_huella("CL-02", uuid4().hex, {"a": 1, "b": 2}) != calcular_huella("CL-02", "otro", {"a": 1, "b": 2})
    objeto = uuid4()
    assert calcular_huella("CL-02", objeto, {"a": 1, "b": 2}) == calcular_huella("CL-02", objeto, {"b": 2, "a": 1})


def test_un_rechazo_queda_guardado_con_su_clave():
    tienda, (producto,) = tienda_con_productos(1)
    picker = nuevo_usuario(tienda, "PICKER")
    clave = uuid4()
    r = client.post(f"/v1/darkstores/{tienda}/productos/{producto}/danos",
                    headers={"Clave-Operacion": str(clave), "Usuario-Id": str(picker)},
                    json={"cantidad": 1, "motivo": "x"})
    assert r.status_code == 403
    with pool.connection() as conn:
        fila = conn.execute("SELECT resultado, motivo_rechazo FROM registro_operacion WHERE clave_operacion = %s", (clave,)).fetchone()
    assert fila == ("RECHAZADA", "SIN_PERMISO")


def test_procesar_eventos_cuenta_los_que_proceso():
    while pedidos.procesar_eventos_pendientes():
        pass
    tienda, producto = preparar_escenario(3)
    cliente = nuevo_cliente()
    pedido = client.post("/v1/checkouts", headers={"Clave-Operacion": str(uuid4()), "Cliente-Id": str(cliente)},
                         json={"darkstore_id": str(tienda), "lineas": [{"producto_id": str(producto), "cantidad": 1}]}).json()["pedido"]
    intento_id = uuid4()
    with pool.connection() as conn, conn.transaction():
        conn.execute("INSERT INTO intento_pago (intento_id, pedido_id, monto, estado, fecha_resultado) "
                     "VALUES (%s, %s, %s, 'APROBADO', clock_timestamp())", (intento_id, pedido["pedido_id"], pedido["total_vigente"]))
        conn.execute("INSERT INTO evento_interno (tipo, productor, intento_id, procesado) VALUES ('EV-01', 'M04', %s, false)", (intento_id,))
    assert pedidos.procesar_eventos_pendientes() == 1


def test_confirmar_dos_veces_el_mismo_intento_no_reembolsa_como_duplicado():
    tienda, (producto,) = tienda_con_productos(1)
    pedido_id, _, intento_id = pedido_pagado(tienda, [(producto, 1)])
    with pool.connection() as conn, conn.transaction():
        assert pedidos.confirmar_compra(conn, intento_id) is None
    assert reembolsos_de(intento_id) == []


def test_separar_mas_de_lo_que_pide_la_linea_aunque_haya_existencias():
    tienda, (producto,) = tienda_con_productos(1, unidades=10)
    pedido_id, cliente, _ = pedido_pagado(tienda, [(producto, 2)])
    linea = client.get(f"/v1/portal/pedido/{pedido_id}", headers={"Cliente-Id": str(cliente)}).json()["lineas"][0]["linea_id"]
    picker = nuevo_usuario(tienda, "PICKER")
    r = client.post(f"/v1/lineas-pedido/{linea}/separaciones", headers={"Clave-Operacion": str(uuid4()), "Usuario-Id": str(picker)},
                    json={"cantidad": 3})
    assert r.status_code == 409 and r.json()["codigo"] == "ESTADO_INCOMPATIBLE"


def test_checkout_y_cierre_responden_los_ids_correctos():
    tienda, producto = preparar_escenario(3)
    cliente = nuevo_cliente()
    checkout = client.post("/v1/checkouts", headers={"Clave-Operacion": str(uuid4()), "Cliente-Id": str(cliente)},
                           json={"darkstore_id": str(tienda), "lineas": [{"producto_id": str(producto), "cantidad": 1}]}).json()
    assert checkout["pedido"]["ajuste_pendiente"] is False
    reserva = checkout["reserva"]["reserva_id"]
    cierre = client.post(f"/v1/reservas/{reserva}/cierre", headers={"Clave-Operacion": str(uuid4()), "Cliente-Id": str(cliente)},
                         json={"motivo": "CANCELACION_CLIENTE"}).json()
    assert (cierre["reserva_id"], cierre["pedido_id"]) == (reserva, checkout["pedido"]["pedido_id"])


def test_danar_exactamente_lo_que_hay_si_se_permite():
    tienda, (producto,) = tienda_con_productos(1, unidades=3)
    personal = nuevo_usuario(tienda, "PERSONAL_TIENDA")
    assert danar(tienda, producto, personal, 3).status_code == 201
    assert inventario(tienda, producto)["e"] == 0


def test_un_dano_que_deja_justo_lo_comprometido_no_abre_discrepancia():
    tienda, (producto,) = tienda_con_productos(1, unidades=5)
    reservar(tienda, producto, 3)
    personal = nuevo_usuario(tienda, "PERSONAL_TIENDA")
    r = danar(tienda, producto, personal, 2)
    assert r.json()["discrepancia_id"] is None
    assert inventario(tienda, producto)["estado"] == "HABILITADO"


def test_el_error_dice_cuantas_unidades_quedan_por_inspeccionar():
    tienda, (producto,) = tienda_con_productos(1)
    personal = nuevo_usuario(tienda, "PERSONAL_TIENDA")
    inspector = nuevo_usuario(tienda, "INSPECTOR")
    dano = danar(tienda, producto, personal, 3).json()["movimiento_id"]
    inspeccionar(dano, inspector, 1, "APTA")
    assert "Solo quedan 2 unidades" in inspeccionar(dano, inspector, 3, "APTA").json()["mensaje"]


def test_cancelar_un_pedido_con_una_linea_ya_cancelada_solo_cancela_lo_pendiente():
    from escenarios_c3 import aplicar_faltante, crear_resolucion_faltante, estado_pedido, lineas_de

    tienda, (a, b) = tienda_con_productos(2, unidades=5)
    pedido_id, cliente, intento_id = pedido_pagado(tienda, [(a, 1), (b, 2)])
    linea_a = lineas_de(pedido_id)[a]["linea_id"]
    resolucion = crear_resolucion_faltante(tienda, a, linea_a, 1)
    aplicar_faltante(resolucion, linea_a)
    r = client.post(f"/v1/pedidos/{pedido_id}/cancelacion", headers={"Clave-Operacion": str(uuid4()), "Cliente-Id": str(cliente)},
                    json={"motivo": "ya no"})
    assert r.status_code == 200, r.text
    assert r.json()["monto_reembolso"] == "200.00"
    assert estado_pedido(pedido_id) == "CANCELADO"
    assert sum(m for _, m, *_ in reembolsos_de(intento_id)) == Decimal("300.00")


def test_una_asignacion_sin_canceladas_no_cancela_nada():
    from escenarios_c3 import aplicar_faltante, crear_resolucion_faltante, lineas_de

    tienda, (producto,) = tienda_con_productos(1, unidades=5)
    pedido_id, _, intento_id = pedido_pagado(tienda, [(producto, 2)])
    linea = lineas_de(pedido_id)[producto]["linea_id"]
    aplicar_faltante(crear_resolucion_faltante(tienda, producto, linea, 0), linea)
    assert lineas_de(pedido_id)[producto]["k"] == 0
    assert reembolsos_de(intento_id) == []


def test_limites_del_monto():
    import pytest as _pytest

    from app.errores import ErrorDeNegocio
    from app.modulos.pagos import MONTO_MAXIMO, _parsear_dinero

    assert _parsear_dinero("0.01") == Decimal("0.01")
    assert _parsear_dinero("0.50") == Decimal("0.50")
    assert _parsear_dinero(str(MONTO_MAXIMO)) == MONTO_MAXIMO
    for malo in ("0", "0.00", "-1", str(MONTO_MAXIMO + Decimal("0.01")), "1.001"):
        with _pytest.raises(ErrorDeNegocio):
            _parsear_dinero(malo)


def test_el_reintento_por_interbloqueo_se_rinde_al_tercer_intento():
    import pytest as _pytest
    from psycopg.errors import DeadlockDetected

    from app.modulos.pagos import _ejecutar_con_reintento_concurrencia

    llamadas = []

    def siempre():
        llamadas.append(1)
        raise DeadlockDetected()

    with _pytest.raises(DeadlockDetected):
        _ejecutar_con_reintento_concurrencia(siempre)
    assert len(llamadas) == 3


def test_no_se_paga_el_pedido_de_otro_cliente():
    tienda, producto = preparar_escenario(3)
    dueno = nuevo_cliente()
    pedido = client.post("/v1/checkouts", headers={"Clave-Operacion": str(uuid4()), "Cliente-Id": str(dueno)},
                         json={"darkstore_id": str(tienda), "lineas": [{"producto_id": str(producto), "cantidad": 1}]}).json()["pedido"]
    r = client.post(f"/v1/pedidos/{pedido['pedido_id']}/intentos-pago",
                    headers={"Clave-Operacion": str(uuid4()), "Cliente-Id": str(nuevo_cliente())},
                    json={"monto": pedido["total_vigente"]})
    assert r.status_code == 404


def test_un_reembolso_solo_se_envia_una_vez_y_solo_el_que_esta_al_frente(monkeypatch):
    from app.modulos import pasarela_simulada
    from app.modulos.pagos import enviar_reembolso, solicitar_reembolso
    from test_c2_reembolsos import _preparar_intento_aprobado

    pedido_id, intento_id = _preparar_intento_aprobado(Decimal("200.00"))
    with pool.connection() as conn, conn.transaction():
        reserva, producto = conn.execute(
            "SELECT lr.reserva_id, lr.producto_id FROM linea_reserva lr JOIN pedido p USING (reserva_id) WHERE p.pedido_id = %s",
            (pedido_id,)).fetchone()
        primero = solicitar_reembolso(conn, intento_id, Decimal("10.00"), "A")
        solicitar_reembolso(conn, intento_id, Decimal("20.00"), "B", reserva_origen_id=reserva, producto_origen_id=producto)
        segundo = conn.execute("SELECT reembolso_id FROM reembolso WHERE intento_id = %s AND motivo = 'B'", (intento_id,)).fetchone()[0]
    llamadas = []
    original = pasarela_simulada.reembolsar
    monkeypatch.setattr(pasarela_simulada, "reembolsar", lambda r, m: llamadas.append(r) or original(r, m))
    pasarela_simulada._estado_simulador["reembolso"] = "NO_RESPONDER"
    try:
        enviar_reembolso(segundo)
        enviar_reembolso(primero)
        enviar_reembolso(primero)
    finally:
        pasarela_simulada.reiniciar_simulador()
    assert llamadas == [primero]


def test_una_confirmacion_tardia_despues_de_un_fallo_se_respeta():
    from app.modulos import pasarela_simulada
    from app.modulos.pagos import enviar_reembolso, solicitar_reembolso
    from test_c2_reembolsos import _preparar_intento_aprobado

    _, intento_id = _preparar_intento_aprobado(Decimal("200.00"))
    with pool.connection() as conn, conn.transaction():
        reembolso = solicitar_reembolso(conn, intento_id, Decimal("200.00"), "PRUEBA")
    pasarela_simulada._estado_simulador["reembolso"] = "NO_RESPONDER"
    try:
        enviar_reembolso(reembolso)
    finally:
        pasarela_simulada.reiniciar_simulador()
    notificar = lambda resultado, ref: client.post("/v1/pasarela/notificaciones", json={
        "objeto": "REEMBOLSO", "objeto_id": str(reembolso), "referencia_externa": ref, "resultado": resultado})
    assert notificar("FALLIDO", "PSP-F1").json()["estado_aplicado"] == "REEMBOLSO_FALLIDO"
    assert notificar("FALLIDO", "PSP-F2").json()["estado_aplicado"] == "REEMBOLSO_FALLIDO"
    assert notificar("CONFIRMADO", "PSP-C1").json()["estado_aplicado"] == "REEMBOLSADO_TOTAL"
