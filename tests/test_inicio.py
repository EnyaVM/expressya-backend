from fastapi.testclient import TestClient

from app.main import app

cliente = TestClient(app)

CERO = "00000000-0000-0000-0000-000000000000"


def test_la_aplicacion_llega_a_la_base():
    respuesta = cliente.get("/v1/salud")
    assert respuesta.status_code == 200
    assert respuesta.json()["estado"] == "ok"


def test_producto_inexistente_responde_404():
    respuesta = cliente.get(f"/v1/darkstores/{CERO}/productos/{CERO}/disponibilidad")
    assert respuesta.status_code == 404
    assert respuesta.json()["codigo"] == "NO_ENCONTRADO"
