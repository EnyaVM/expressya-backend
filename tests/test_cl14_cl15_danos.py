from uuid import uuid4

from escenarios_c3 import tienda_con_productos
from escenarios_danos import danar, inspeccionar, resolver
from escenarios_faltantes import discrepancias, historial_cuadra, inventario, nuevo_usuario, reservar


def tienda_con_personal(unidades=10):
    tienda, (producto,) = tienda_con_productos(1, unidades=unidades)
    personal = nuevo_usuario(tienda, "PERSONAL_TIENDA")
    inspector = nuevo_usuario(tienda, "INSPECTOR")
    autorizador = nuevo_usuario(tienda, "AUTORIZADOR")
    return tienda, producto, personal, inspector, autorizador


def saldos(tienda, producto):
    i = inventario(tienda, producto)
    return i["e"], i["n"], i["d"], i["estado"]


def test_reportar_un_dano_pasa_unidades_de_existencias_a_no_vendibles():
    tienda, producto, personal, *_ = tienda_con_personal()

    r = danar(tienda, producto, personal, 2)

    assert r.status_code == 201, r.text
    assert r.json()["tipo"] == "DANO"
    assert sorted((s["saldo"], s["cantidad"]) for s in r.json()["saldos"]) == [("E", -2), ("N", 2)]
    assert r.json()["discrepancia_id"] is None
    assert saldos(tienda, producto) == (8, 2, 8, "HABILITADO")
    assert historial_cuadra(tienda, producto)


def test_no_se_dana_mas_de_lo_que_hay():
    tienda, producto, personal, *_ = tienda_con_personal(unidades=3)
    r = danar(tienda, producto, personal, 4)
    assert r.status_code == 422
    assert r.json()["codigo"] == "CANTIDAD_INVALIDA"
    assert saldos(tienda, producto) == (3, 0, 3, "HABILITADO")


def test_solo_el_personal_de_tienda_reporta_danos():
    tienda, producto, *_ = tienda_con_personal()
    picker = nuevo_usuario(tienda, "PICKER")
    assert danar(tienda, producto, picker, 1).status_code == 403


def test_un_dano_que_deja_deficit_bloquea_y_abre_discrepancia_d05():
    tienda, producto, personal, *_ = tienda_con_personal(unidades=3)
    reservar(tienda, producto, 3)

    r = danar(tienda, producto, personal, 2)

    assert r.status_code == 201, r.text
    assert r.json()["discrepancia_id"]
    assert [(d[1], d[2]) for d in discrepancias(tienda, producto)] == [("DANO", "EN_VALIDACION")]
    e, n, d, estado = saldos(tienda, producto)
    assert (e, n, estado) == (1, 2, "BLOQUEADO")
    assert inventario(tienda, producto)["r"] == 0
    assert historial_cuadra(tienda, producto)


def test_at07_reincorporar_lo_apto_exige_una_segunda_persona():
    tienda, producto, personal, inspector, autorizador = tienda_con_personal()
    dano = danar(tienda, producto, personal, 2).json()["movimiento_id"]
    inspeccion = inspeccionar(dano, inspector, 2, "APTA")
    assert inspeccion.status_code == 201, inspeccion.text

    r = resolver(inspeccion.json()["inspeccion_id"], autorizador, "REINCORPORAR")

    assert r.status_code == 201, r.text
    assert r.json()["tipo"] == "REINCORPORACION"
    assert saldos(tienda, producto) == (10, 0, 10, "HABILITADO")
    assert historial_cuadra(tienda, producto)


def test_quien_inspecciona_no_puede_autorizar_su_propia_inspeccion():
    tienda, producto, personal, *_ = tienda_con_personal()
    ambos = nuevo_usuario(tienda, "INSPECTOR", "AUTORIZADOR")
    dano = danar(tienda, producto, personal, 1).json()["movimiento_id"]
    inspeccion = inspeccionar(dano, ambos, 1, "APTA").json()["inspeccion_id"]

    r = resolver(inspeccion, ambos, "REINCORPORAR")

    assert r.status_code == 422
    assert r.json()["codigo"] == "AUTORIZACION_INVALIDA"
    assert saldos(tienda, producto) == (9, 1, 9, "HABILITADO")


def test_lo_no_apto_se_desecha_y_no_se_puede_reincorporar():
    tienda, producto, personal, inspector, autorizador = tienda_con_personal()
    dano = danar(tienda, producto, personal, 3).json()["movimiento_id"]
    inspeccion = inspeccionar(dano, inspector, 3, "NO_APTA").json()["inspeccion_id"]

    mal = resolver(inspeccion, autorizador, "REINCORPORAR")
    bien = resolver(inspeccion, autorizador, "DESECHAR")

    assert mal.status_code == 422 and mal.json()["codigo"] == "AUTORIZACION_INVALIDA"
    assert bien.status_code == 201 and bien.json()["tipo"] == "DESECHO"
    assert saldos(tienda, producto) == (7, 0, 7, "HABILITADO")
    assert historial_cuadra(tienda, producto)


def test_una_inspeccion_se_resuelve_una_sola_vez():
    tienda, producto, personal, inspector, autorizador = tienda_con_personal()
    dano = danar(tienda, producto, personal, 2).json()["movimiento_id"]
    inspeccion = inspeccionar(dano, inspector, 2, "APTA").json()["inspeccion_id"]
    clave = uuid4()

    primera = resolver(inspeccion, autorizador, "REINCORPORAR", clave)
    repetida = resolver(inspeccion, autorizador, "REINCORPORAR", clave)
    otra = resolver(inspeccion, autorizador, "REINCORPORAR")

    assert repetida.json() == primera.json()
    assert repetida.headers.get("Resultado-Previo") == "true"
    assert otra.status_code == 409 and otra.json()["codigo"] == "ESTADO_INCOMPATIBLE"
    assert saldos(tienda, producto) == (10, 0, 10, "HABILITADO")


def test_no_se_inspecciona_mas_de_lo_danado():
    tienda, producto, personal, inspector, _ = tienda_con_personal()
    dano = danar(tienda, producto, personal, 2).json()["movimiento_id"]
    assert inspeccionar(dano, inspector, 1, "APTA").status_code == 201

    r = inspeccionar(dano, inspector, 2, "NO_APTA")

    assert r.status_code == 422
    assert r.json()["codigo"] == "CANTIDAD_INVALIDA"
    assert inspeccionar(dano, inspector, 1, "NO_APTA").status_code == 201


def test_roles_de_inspeccion_y_autorizacion():
    tienda, producto, personal, inspector, autorizador = tienda_con_personal()
    dano = danar(tienda, producto, personal, 1).json()["movimiento_id"]
    assert inspeccionar(dano, autorizador, 1, "APTA").status_code == 403
    inspeccion = inspeccionar(dano, inspector, 1, "APTA").json()["inspeccion_id"]
    assert resolver(inspeccion, personal, "REINCORPORAR").status_code == 403


def test_inspeccionar_algo_que_no_es_un_dano_da_404():
    tienda, producto, _, inspector, _ = tienda_con_personal()
    assert inspeccionar(uuid4(), inspector, 1, "APTA").status_code == 404
