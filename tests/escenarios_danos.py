from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def danar(tienda, producto, usuario, cantidad, clave=None):
    return client.post(
        f"/v1/darkstores/{tienda}/productos/{producto}/danos",
        headers={"Clave-Operacion": str(clave or uuid4()), "Usuario-Id": str(usuario)},
        json={"cantidad": cantidad, "motivo": "Caja aplastada al descargar"},
    )


def inspeccionar(movimiento_id, usuario, cantidad, resultado):
    return client.post(
        f"/v1/danos/{movimiento_id}/inspecciones",
        headers={"Clave-Operacion": str(uuid4()), "Usuario-Id": str(usuario)},
        json={"cantidad": cantidad, "resultado": resultado, "evidencia": "Foto de la prenda revisada"},
    )


def resolver(inspeccion_id, usuario, accion, clave=None):
    return client.post(
        f"/v1/inspecciones/{inspeccion_id}/resolucion",
        headers={"Clave-Operacion": str(clave or uuid4()), "Usuario-Id": str(usuario)},
        json={"accion": accion},
    )
