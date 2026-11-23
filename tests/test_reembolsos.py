
from decimal import Decimal
import pytest


def procesar_reembolso(
    total_cobrado,
    total_reembolsado,
    importe,
    operaciones,
    clave
):
    """
    Simula las reglas de negocio de los reembolsos.
    """

    total_cobrado = Decimal(str(total_cobrado))
    total_reembolsado = Decimal(str(total_reembolsado))
    importe = Decimal(str(importe))

    # Validar importe.
    if importe <= 0:
        raise ValueError("IMPORTE_INVALIDO")

    # AT-09: evitar operaciones duplicadas.
    if clave in operaciones:
        return {
            "estado": "YA_REEMBOLSADO",
            "importe": operaciones[clave]
        }

    # Validar que no se supere el cobro.
    disponible = total_cobrado - total_reembolsado

    if importe > disponible:
        raise ValueError(
            "REEMBOLSO_SUPERA_IMPORTE_COBRADO"
        )

    # Registrar el reembolso.
    operaciones[clave] = importe

    return {
        "estado": "REEMBOLSO_REGISTRADO",
        "importe": importe
    }


# C4 - PRUEBA 1
def test_reembolso_correcto():
    operaciones = {}

    resultado = procesar_reembolso(
        total_cobrado=500,
        total_reembolsado=0,
        importe=200,
        operaciones=operaciones,
        clave="REF-001"
    )

    assert resultado["estado"] == "REEMBOLSO_REGISTRADO"
    assert resultado["importe"] == Decimal("200")


# C4 - PRUEBA 2
def test_evitar_reembolso_duplicado():
    operaciones = {}

    procesar_reembolso(
        500, 0, 200, operaciones, "REF-002"
    )

    resultado = procesar_reembolso(
        500, 0, 200, operaciones, "REF-002"
    )

    assert resultado["estado"] == "YA_REEMBOLSADO"
    assert len(operaciones) == 1


# C4 - PRUEBA 3
def test_no_superar_total_cobrado():
    operaciones = {}

    with pytest.raises(ValueError) as error:
        procesar_reembolso(
            500,
            400,
            150,
            operaciones,
            "REF-003"
        )

    assert str(error.value) == (
        "REEMBOLSO_SUPERA_IMPORTE_COBRADO"
    )


# C4 - PRUEBA 4
@pytest.mark.parametrize("importe", [-100, 0])
def test_rechazar_importes_invalidos(importe):
    operaciones = {}

    with pytest.raises(ValueError) as error:
        procesar_reembolso(
            500,
            0,
            importe,
            operaciones,
            "REF-004"
        )

    assert str(error.value) == "IMPORTE_INVALIDO"


# C4 - PRUEBA 5
def test_varios_reembolsos_parciales():
    operaciones = {}

    primer_reembolso = procesar_reembolso(
        500, 0, 100, operaciones, "REF-005"
    )

    segundo_reembolso = procesar_reembolso(
        500, 100, 200, operaciones, "REF-006"
    )

    total = (
        primer_reembolso["importe"]
        + segundo_reembolso["importe"]
    )

    assert total == Decimal("300")
    assert total <= Decimal("500")
    assert len(operaciones) == 2
