
from decimal import Decimal


def test_flujo_completo_expressya(
    escenario_expressya
):
    """
    Prueba E2E de C4.

    Comprueba el proceso completo de compra,
    pago, faltante, cancelacion y reembolso.
    """

    sistema = escenario_expressya

    # 1. CHECKOUT
    pedido = sistema.checkout(
        cliente_id=101,
        productos=[
            {
                "producto_id": 1,
                "cantidad": 5,
                "precio": Decimal("100.00")
            }
        ]
    )

    assert pedido["estado"] in (
        "CREADO", "PENDIENTE_PAGO"
    )

    pedido_id = pedido["pedido_id"]

    # 2. PAGO
    pago = sistema.pagar(
        pedido_id=pedido_id,
        importe=Decimal("500.00")
    )

    assert pago["estado"] == "APROBADO"

    # 3. PREPARACION
    preparacion = sistema.iniciar_preparacion(
        pedido_id=pedido_id
    )

    assert preparacion["estado"] == "PREPARACION_INICIADA"

    # 4. DETECTAR FALTANTE
    faltante = sistema.registrar_faltante(
        pedido_id=pedido_id,
        producto_id=1,
        cantidad=2
    )

    assert faltante["estado"] == "DISCREPANCIA_REGISTRADA"

    # 5. CANCELACION
    cancelacion = sistema.cancelar(
        pedido_id=pedido_id,
        producto_id=1,
        cantidad=2,
        motivo="FALTANTE",
        clave_operacion="E2E-CANCEL-001"
    )

    assert cancelacion["cantidad_cancelada"] == 2

    # 6. REEMBOLSO
    reembolso = sistema.consultar_reembolsos(
        pedido_id=pedido_id
    )

    assert Decimal(
        str(reembolso["total"])
    ) == Decimal("200.00")

    # 7. AVISO AL CLIENTE
    aviso = sistema.consultar_avisos(
        pedido_id=pedido_id
    )

    assert aviso["existe"] is True

    # 8. VALIDAR RESULTADO FINAL
    resultado = sistema.consultar_pedido(
        pedido_id=pedido_id
    )

    assert resultado["cantidad_surtida"] == 3
    assert resultado["cantidad_cancelada"] == 2

    assert Decimal(
        str(resultado["total_reembolsado"])
    ) == Decimal("200.00")

    assert Decimal(
        str(resultado["total_cobrado"])
    ) == Decimal("500.00")

    assert Decimal(
        str(resultado["importe_final"])
    ) == Decimal("300.00")
