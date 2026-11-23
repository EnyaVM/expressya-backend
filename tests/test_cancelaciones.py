
import pytest


# Simulación del módulo de cancelaciones
class ServicioCancelaciones:

    def __init__(self):
        self.pedidos = {}
        self.cancelaciones = []

    def crear_pedido(self, pedido_id, cantidad, precio):
        self.pedidos[pedido_id] = {
            "cantidad": cantidad,
            "precio": precio,
            "estado": "PAGADO",
            "preparacion_iniciada": False,
            "total_cancelado": 0,
            "total_reembolsado": 0
        }

    def iniciar_preparacion(self, pedido_id):
        self.pedidos[pedido_id]["preparacion_iniciada"] = True
        self.pedidos[pedido_id]["estado"] = "PREPARACION_INICIADA"

    def cancelar(self, pedido_id, cantidad, motivo, clave):
        pedido = self.pedidos[pedido_id]

        # Evitar cancelaciones duplicadas
        for cancelacion in self.cancelaciones:
            if cancelacion["clave"] == clave:
                return cancelacion

        # No permitir cancelar después de iniciar preparación
        if pedido["preparacion_iniciada"]:
            raise ValueError("409 PREPARACION_INICIADA")

        disponibles = pedido["cantidad"] - pedido["total_cancelado"]

        if cantidad <= 0:
            raise ValueError("La cantidad debe ser mayor a cero")

        if cantidad > disponibles:
            raise ValueError("Cantidad de cancelación no válida")

        importe = cantidad * pedido["precio"]

        cancelacion = {
            "pedido_id": pedido_id,
            "cantidad": cantidad,
            "motivo": motivo,
            "importe": importe,
            "clave": clave
        }

        pedido["total_cancelado"] += cantidad
        pedido["total_reembolsado"] += importe

        self.cancelaciones.append(cancelacion)

        return cancelacion


@pytest.fixture
def servicio():
    sistema = ServicioCancelaciones()
    sistema.crear_pedido(
        pedido_id=100,
        cantidad=5,
        precio=100
    )
    return sistema


# 1. Cancelación por falta de inventario
def test_cancelacion_por_faltante(servicio):
    resultado = servicio.cancelar(
        100, 2, "FALTA_INVENTARIO", "CLAVE-001"
    )

    assert resultado["cantidad"] == 2
    assert resultado["importe"] == 200
    assert servicio.pedidos[100]["total_cancelado"] == 2


# 2. Cancelación solicitada por el cliente
def test_cancelacion_por_cliente(servicio):
    resultado = servicio.cancelar(
        100, 1, "SOLICITUD_CLIENTE", "CLAVE-002"
    )

    assert resultado["motivo"] == "SOLICITUD_CLIENTE"
    assert resultado["cantidad"] == 1


# 3. No permitir cancelar después de iniciar preparación
def test_rechazar_cancelacion_con_preparacion_iniciada(servicio):
    servicio.iniciar_preparacion(100)

    with pytest.raises(ValueError, match="409 PREPARACION_INICIADA"):
        servicio.cancelar(
            100, 1, "SOLICITUD_CLIENTE", "CLAVE-003"
        )


# 4. Evitar cancelaciones duplicadas
def test_evitar_cancelacion_duplicada(servicio):
    primera = servicio.cancelar(
        100, 2, "FALTA_INVENTARIO", "CLAVE-004"
    )

    segunda = servicio.cancelar(
        100, 2, "FALTA_INVENTARIO", "CLAVE-004"
    )

    assert primera == segunda
    assert len(servicio.cancelaciones) == 1
    assert servicio.pedidos[100]["total_cancelado"] == 2


# 5. No permitir cancelar más unidades de las solicitadas
def test_rechazar_cantidad_superior(servicio):
    with pytest.raises(ValueError, match="Cantidad de cancelación no válida"):
        servicio.cancelar(
            100, 6, "FALTA_INVENTARIO", "CLAVE-005"
        )


# 6. No permitir cantidades iguales o menores a cero
@pytest.mark.parametrize("cantidad", [0, -1])
def test_rechazar_cantidad_invalida(servicio, cantidad):
    with pytest.raises(ValueError, match="mayor a cero"):
        servicio.cancelar(
            100, cantidad, "SOLICITUD_CLIENTE", "CLAVE-006"
        )


# 7. Permitir cancelaciones parciales sucesivas
def test_cancelaciones_parciales(servicio):
    servicio.cancelar(
        100, 2, "FALTA_INVENTARIO", "CLAVE-007"
    )

    servicio.cancelar(
        100, 1, "SOLICITUD_CLIENTE", "CLAVE-008"
    )

    assert servicio.pedidos[100]["total_cancelado"] == 3
    assert servicio.pedidos[100]["total_reembolsado"] == 300


# 8. No permitir cancelar más unidades que las restantes
def test_rechazar_exceso_en_cancelaciones_parciales(servicio):
    servicio.cancelar(
        100, 3, "FALTA_INVENTARIO", "CLAVE-009"
    )

    with pytest.raises(ValueError, match="Cantidad de cancelación no válida"):
        servicio.cancelar(
            100, 3, "SOLICITUD_CLIENTE", "CLAVE-010"
        )
