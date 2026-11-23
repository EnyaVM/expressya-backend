
import pytest


# Simulación del módulo de avisos
class ServicioAvisos:

    def __init__(self):
        self.avisos = []

    def enviar_aviso(self, cliente_id, pedido_id, mensaje, tipo):
        # Evitar avisos duplicados
        for aviso in self.avisos:
            if (
                aviso["pedido_id"] == pedido_id
                and aviso["tipo"] == tipo
                and aviso["mensaje"] == mensaje
            ):
                return aviso

        aviso = {
            "cliente_id": cliente_id,
            "pedido_id": pedido_id,
            "mensaje": mensaje,
            "tipo": tipo,
            "estado": "ENVIADO"
        }

        self.avisos.append(aviso)
        return aviso

    def consultar_avisos(self, pedido_id):
        return [
            aviso for aviso in self.avisos
            if aviso["pedido_id"] == pedido_id
        ]


@pytest.fixture
def servicio():
    return ServicioAvisos()


# 1. Comprobar que se envíe un aviso correctamente
def test_enviar_aviso_correctamente(servicio):
    resultado = servicio.enviar_aviso(
        cliente_id=1,
        pedido_id=100,
        mensaje="Dos productos fueron cancelados por falta de inventario.",
        tipo="CANCELACION"
    )

    assert resultado["estado"] == "ENVIADO"
    assert resultado["cliente_id"] == 1
    assert resultado["pedido_id"] == 100


# 2. Comprobar que el aviso tenga el mensaje correcto
def test_mensaje_de_cancelacion(servicio):
    mensaje = "Tu pedido fue actualizado por falta de productos."

    resultado = servicio.enviar_aviso(
        1, 100, mensaje, "CANCELACION"
    )

    assert resultado["mensaje"] == mensaje


# 3. Evitar avisos duplicados
def test_evitar_aviso_duplicado(servicio):
    primero = servicio.enviar_aviso(
        1, 100, "Se cancelaron dos productos.", "CANCELACION"
    )

    segundo = servicio.enviar_aviso(
        1, 100, "Se cancelaron dos productos.", "CANCELACION"
    )

    assert primero == segundo
    assert len(servicio.avisos) == 1


# 4. Consultar los avisos de un pedido
def test_consultar_avisos(servicio):
    servicio.enviar_aviso(
        1, 100, "Pedido actualizado.", "CANCELACION"
    )

    servicio.enviar_aviso(
        1, 101, "Pedido enviado.", "ESTADO_PEDIDO"
    )

    resultados = servicio.consultar_avisos(100)

    assert len(resultados) == 1
    assert resultados[0]["pedido_id"] == 100


# 5. Comprobar que dos pedidos puedan generar avisos
def test_avisos_de_diferentes_pedidos(servicio):
    servicio.enviar_aviso(
        1, 100, "Faltan productos.", "CANCELACION"
    )

    servicio.enviar_aviso(
        2, 200, "Tu pedido está listo.", "ESTADO_PEDIDO"
    )

    assert len(servicio.avisos) == 2


# 6. Comprobar el tipo de aviso
def test_tipo_de_aviso(servicio):
    resultado = servicio.enviar_aviso(
        1, 100, "Se realizó un reembolso.", "REEMBOLSO"
    )

    assert resultado["tipo"] == "REEMBOLSO"
