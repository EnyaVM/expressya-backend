import logging

from app.main import ejecutar_ciclo, expirar_reservas, procesar_pagos


def test_un_ciclo_que_falla_se_registra_y_no_tumba_el_servidor(caplog):
    def falla():
        raise RuntimeError("base caida")

    with caplog.at_level(logging.ERROR):
        assert ejecutar_ciclo(falla, logging.getLogger("prueba")) is False
    assert "Fallo el ciclo falla" in caplog.text


def test_los_ciclos_reales_corren_sin_error():
    bitacora = logging.getLogger("prueba")
    assert ejecutar_ciclo(expirar_reservas, bitacora) is True
    assert ejecutar_ciclo(procesar_pagos, bitacora) is True


def test_el_ciclo_de_vencimiento_expira_una_reserva_vencida(caplog):
    from escenarios import crear_reserva_vencida, estados, nuevo_cliente, preparar_escenario

    tienda, producto = preparar_escenario(3)
    _, reserva = crear_reserva_vencida(nuevo_cliente(), tienda, producto)
    with caplog.at_level(logging.INFO, logger="expressya.vencimiento"):
        expirar_reservas()
    assert estados(reserva)[0] == "EXPIRADA"
    assert "Reservas expiradas" in caplog.text
