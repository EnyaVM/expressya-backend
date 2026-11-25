from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.db import pool
from app.main import app
from app.modulos.cuentas import crear_cuenta
from escenarios_c3 import tienda_con_productos

CLAVE = "clave-segura-123"


@pytest.fixture(autouse=True)
def produccion(monkeypatch):
    monkeypatch.setenv("EXPRESSYA_ENCABEZADOS_DE_PRUEBA", "0")


def nombre_usuario(prefijo):
    return f"{prefijo}.{uuid4().hex[:10]}"


def cuenta(tipo, asignaciones=()):
    usuario = nombre_usuario(tipo.lower())
    with pool.connection() as conn:
        crear_cuenta(conn, usuario, f"Persona {tipo.title()}", CLAVE, tipo, asignaciones)
    return usuario


def entrar(usuario, portal, clave=CLAVE):
    cliente = TestClient(app)
    respuesta = cliente.post("/v1/auth/login", json={"usuario": usuario, "clave": clave, "portal": portal})
    return cliente, respuesta


def sesion(usuario, portal):
    cliente, respuesta = entrar(usuario, portal)
    assert respuesta.status_code == 200, respuesta.text
    cliente.headers["X-CSRF-Token"] = respuesta.json()["csrf"]
    return cliente


def checkout(cliente, tienda, producto, cabeceras=None):
    return cliente.post(
        "/v1/checkouts",
        headers={"Clave-Operacion": str(uuid4()), **(cabeceras or {})},
        json={"darkstore_id": str(tienda), "lineas": [{"producto_id": str(producto), "cantidad": 1}]},
    )


def test_un_cliente_se_registra_entra_y_compra_sin_mandar_su_id():
    tienda, (producto,) = tienda_con_productos(1)
    anonimo = TestClient(app)
    usuario = nombre_usuario("ana")
    alta = anonimo.post("/v1/auth/registro", json={"usuario": usuario, "nombre": "Ana López", "clave": CLAVE})
    assert alta.status_code == 201, alta.text
    cliente = sesion(usuario, "cliente")

    respuesta = checkout(cliente, tienda, producto)

    assert respuesta.status_code == 201, respuesta.text
    pedidos = cliente.get("/v1/portal/pedidos").json()
    assert [p["pedido_id"] for p in pedidos] == [respuesta.json()["pedido"]["pedido_id"]]


def test_sin_sesion_un_id_de_cliente_inventado_no_sirve():
    tienda, (producto,) = tienda_con_productos(1)
    respuesta = checkout(TestClient(app), tienda, producto, {"Cliente-Id": str(uuid4())})
    assert respuesta.status_code == 401
    assert respuesta.json()["codigo"] == "NO_AUTENTICADO"


def test_con_sesion_no_se_puede_suplantar_a_otro_cliente():
    tienda, (producto,) = tienda_con_productos(1)
    ana = sesion(cuenta("CLIENTE"), "cliente")
    otra = sesion(cuenta("CLIENTE"), "cliente")
    pedido = checkout(otra, tienda, producto).json()["pedido"]["pedido_id"]
    otro_cliente = otra.get(f"/v1/portal/pedido/{pedido}").json()["cliente_id"]

    respuesta = ana.get(f"/v1/portal/pedido/{pedido}", headers={"Cliente-Id": otro_cliente})

    assert respuesta.status_code == 404


def test_clave_incorrecta_responde_igual_que_usuario_inexistente():
    usuario = cuenta("CLIENTE")
    _, mala = entrar(usuario, "cliente", "otra-clave-999")
    _, nadie = entrar("nadie.existe", "cliente")
    assert mala.status_code == nadie.status_code == 401
    assert mala.json() == nadie.json()


def test_la_clave_se_guarda_cifrada():
    usuario = cuenta("CLIENTE")
    with pool.connection() as conn:
        guardada = conn.execute("SELECT clave_hash FROM cuenta WHERE usuario = %s", (usuario,)).fetchone()[0]
    assert CLAVE not in guardada
    assert guardada.startswith("scrypt$")


def test_cada_cuenta_entra_solo_a_su_portal():
    assert entrar(cuenta("CLIENTE"), "interno")[1].status_code == 401
    assert entrar(cuenta("INTERNO", [(tienda_con_productos(1)[0], "PICKER")]), "cliente")[1].status_code == 401
    assert entrar(cuenta("ADMINISTRADOR"), "interno")[1].status_code == 200


def test_una_mutacion_con_cookie_exige_el_token_csrf():
    tienda, (producto,) = tienda_con_productos(1)
    cliente, respuesta = entrar(cuenta("CLIENTE"), "cliente")
    assert respuesta.status_code == 200

    sin_token = checkout(cliente, tienda, producto)

    assert sin_token.status_code == 403
    assert sin_token.json()["codigo"] == "SIN_PERMISO"


def test_el_personal_ve_sus_perfiles_y_opera_solo_con_su_identidad():
    tienda, (producto,) = tienda_con_productos(1)
    picker = sesion(cuenta("INTERNO", [(tienda, "PICKER")]), "interno")

    yo = picker.get("/v1/auth/me").json()
    operacion = picker.get(f"/v1/portal/operacion/{tienda}")

    assert yo["tipo"] == "INTERNO"
    assert [(a["darkstore_id"], a["rol"]) for a in yo["asignaciones"]] == [(str(tienda), "PICKER")]
    assert operacion.status_code == 200
    assert picker.get(f"/v1/portal/operacion/{tienda_con_productos(1)[0]}").status_code == 403


def test_administracion_consulta_todo_pero_no_opera():
    tienda, (producto,) = tienda_con_productos(1)
    admin = sesion(cuenta("ADMINISTRADOR"), "interno")

    assert admin.get(f"/v1/portal/operacion/{tienda}").status_code == 200
    respuesta = checkout(admin, tienda, producto)
    assert respuesta.status_code == 403
    assert respuesta.json()["codigo"] == "SIN_PERMISO"


def test_administracion_da_de_alta_personal_con_perfiles_y_queda_auditado():
    tienda, _ = tienda_con_productos(1)
    admin = sesion(cuenta("ADMINISTRADOR"), "interno")
    usuario = nombre_usuario("supervisora")

    alta = admin.post("/v1/admin/cuentas", json={
        "usuario": usuario, "nombre": "Sofía Supervisora", "clave": CLAVE, "tipo": "INTERNO",
        "asignaciones": [{"darkstore_id": str(tienda), "rol": "SUPERVISOR"}],
    })

    assert alta.status_code == 201, alta.text
    nueva = sesion(usuario, "interno").get("/v1/auth/me").json()
    assert [a["rol"] for a in nueva["asignaciones"]] == ["SUPERVISOR"]
    auditoria = admin.get("/v1/admin/auditoria").json()
    assert any(a["accion"] == "ALTA_CUENTA" and a["cuenta"] == usuario for a in auditoria)


def test_solo_administracion_gestiona_cuentas():
    tienda, _ = tienda_con_productos(1)
    picker = sesion(cuenta("INTERNO", [(tienda, "PICKER")]), "interno")
    assert picker.get("/v1/admin/cuentas").status_code == 403
    assert TestClient(app).get("/v1/admin/cuentas").status_code == 401


def test_desactivar_una_cuenta_corta_su_sesion_abierta():
    usuario = cuenta("CLIENTE")
    cliente = sesion(usuario, "cliente")
    admin = sesion(cuenta("ADMINISTRADOR"), "interno")
    cuenta_id = cliente.get("/v1/auth/me").json()["cuenta_id"]

    cambio = admin.patch(f"/v1/admin/cuentas/{cuenta_id}", json={"activo": False})

    assert cambio.status_code == 200, cambio.text
    assert cliente.get("/v1/auth/me").status_code == 401
    assert entrar(usuario, "cliente")[1].status_code == 401
    assert any(a["accion"] == "CAMBIO_ACCESO" for a in admin.get("/v1/admin/auditoria").json())


def test_cerrar_sesion_invalida_la_cookie():
    cliente = sesion(cuenta("CLIENTE"), "cliente")
    assert cliente.post("/v1/auth/logout", json={}).status_code == 200
    assert cliente.get("/v1/auth/me").status_code == 401


def test_un_token_bearer_sirve_para_la_api_sin_csrf():
    tienda, (producto,) = tienda_con_productos(1)
    usuario = cuenta("CLIENTE")
    datos = TestClient(app).post("/v1/auth/login", json={"usuario": usuario, "clave": CLAVE, "portal": "cliente", "entregar_token": True}).json()
    api = TestClient(app)

    respuesta = checkout(api, tienda, producto, {"Authorization": f"Bearer {datos['token']}"})

    assert respuesta.status_code == 201, respuesta.text


def test_usuario_repetido_da_409():
    usuario = cuenta("CLIENTE")
    respuesta = TestClient(app).post("/v1/auth/registro", json={"usuario": usuario, "nombre": "Otra", "clave": CLAVE})
    assert respuesta.status_code == 409


def test_clave_corta_se_rechaza():
    respuesta = TestClient(app).post("/v1/auth/registro", json={"usuario": nombre_usuario("x"), "nombre": "X", "clave": "corta"})
    assert respuesta.status_code == 400
