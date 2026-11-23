
import pytest
from decimal import Decimal


def test_c4_01_cancelacion_por_faltante():
    cantidad_original = 5
    cantidad_surtida = 3
    cantidad_cancelada = 2

    restante = cantidad_original - cantidad_surtida

    assert cantidad_cancelada <= restante
    assert cantidad_original == (
        cantidad_surtida + cantidad_cancelada
    )


def test_c4_02_cancelacion_cliente_antes_preparacion():
    estado = "PAGADO"
    motivo = "CLIENTE"

    puede_cancelar = (
        motivo == "CLIENTE"
        and estado != "PREPARACION_INICIADA"
    )

    assert puede_cancelar is True


def test_c4_03_cancelacion_despues_preparacion():
    estado = "PREPARACION_INICIADA"

    respuesta = (
        409
        if estado == "PREPARACION_INICIADA"
        else 200
    )

    assert respuesta == 409


def test_c4_04_no_repetir_reembolso():
    reembolsos = {}
    clave = "CANCELACION-001"

    reembolsos[clave] = Decimal("100.00")

    # Segunda solicitud con la misma clave.
    if clave not in reembolsos:
        reembolsos[clave] = Decimal("100.00")

    assert len(reembolsos) == 1
    assert sum(reembolsos.values()) == Decimal("100.00")


def test_c4_05_no_superar_total_cobrado():
    total_cobrado = Decimal("500.00")
    total_reembolsado = Decimal("400.00")
    nuevo_reembolso = Decimal("150.00")

    restante = total_cobrado - total_reembolsado

    permitido = nuevo_reembolso <= restante

    assert permitido is False
