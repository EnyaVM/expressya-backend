from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app


cliente_http = TestClient(app)
CIERRE = f"/v1/reservas/{uuid4()}/cierre"
CANCELACION = {"motivo": "CANCELACION_CLIENTE"}
CANTIDAD_CERO = {"darkstore_id": str(uuid4()), "lineas": [{"producto_id": str(uuid4()), "cantidad": 0}]}


@pytest.mark.parametrize(("ruta", "cuerpo", "clave"), [
    ("/v1/checkouts", CANTIDAD_CERO, str(uuid4())),
    (CIERRE, {"motivo": "OTRO"}, str(uuid4())),
    (CIERRE, CANCELACION, None),
    (CIERRE, CANCELACION, "invalida"),
    (CIERRE, CANCELACION, "11111111-1111-1111-8111-111111111111"),
    ("/v1/reservas/invalida/cierre", CANCELACION, str(uuid4())),
], ids=["cantidad", "motivo", "sin_clave", "clave_invalida", "clave_no_v4", "reserva_id"])
def test_solicitud_invalida_en_reservas_devuelve_el_error_del_contrato(ruta, cuerpo, clave):
    cabeceras = {"Cliente-Id": str(uuid4())}
    if clave is not None:
        cabeceras["Clave-Operacion"] = clave
    respuesta = cliente_http.post(ruta, headers=cabeceras, json=cuerpo)
    assert respuesta.status_code == 400
    assert respuesta.json()["codigo"] == "SOLICITUD_INVALIDA"
    assert respuesta.json()["mensaje"]
    assert isinstance(respuesta.json()["detalle"], dict)


@pytest.mark.parametrize("ruta", ["/v1/checkouts", CIERRE])
@pytest.mark.parametrize("cliente_id", [None, "incorrecto"])
def test_identificacion_temporal_invalida_da_401(ruta, cliente_id):
    cabeceras = {"Clave-Operacion": str(uuid4())}
    if cliente_id is not None:
        cabeceras["Cliente-Id"] = cliente_id
    respuesta = cliente_http.post(ruta, headers=cabeceras, json={})
    assert respuesta.status_code == 401
    assert respuesta.json()["codigo"] == "NO_AUTENTICADO"


def test_un_cliente_inexistente_no_gasta_la_clave():
    from escenarios import nuevo_cliente, preparar_escenario

    tienda, producto = preparar_escenario(3)
    clave = str(uuid4())
    cuerpo = {"darkstore_id": str(tienda), "lineas": [{"producto_id": str(producto), "cantidad": 1}]}
    falso = cliente_http.post("/v1/checkouts", headers={"Clave-Operacion": clave, "Cliente-Id": str(uuid4())}, json=cuerpo)
    real = cliente_http.post("/v1/checkouts", headers={"Clave-Operacion": clave, "Cliente-Id": str(nuevo_cliente())}, json=cuerpo)
    assert falso.status_code == 401
    assert real.status_code == 201
