
from decimal import Decimal


def reembolsar_cancelacion(
    conn,
    pedido_id,
    importe,
    clave_operacion,
    motivo
):
    importe = Decimal(str(importe))

    if importe <= 0:
        raise ValueError("IMPORTE_INVALIDO")

    # Buscar y bloquear el intento de pago aprobado.
    pago = conn.execute("""
        SELECT intento_id, monto
        FROM intento_pago
        WHERE pedido_id = %s
          AND estado = 'APROBADO'
        ORDER BY intento_id DESC
        LIMIT 1
        FOR UPDATE
    """, (pedido_id,)).fetchone()

    if not pago:
        raise ValueError("PAGO_NO_ENCONTRADO")

    intento_id = pago["intento_id"]
    cobrado = Decimal(str(pago["monto"]))

    # Consultar el importe ya reembolsado.
    acumulado = conn.execute("""
        SELECT COALESCE(SUM(monto), 0) AS total
        FROM reembolso
        WHERE intento_id = %s
    """, (intento_id,)).fetchone()

    ya_reembolsado = Decimal(str(acumulado["total"]))

    # AT-09: impedir superar el importe cobrado.
    restante = cobrado - ya_reembolsado

    if importe > restante:
        raise ValueError("REEMBOLSO_SUPERA_IMPORTE_COBRADO")

    # Evitar duplicados mediante la clave de operación.
    existente = conn.execute("""
        SELECT reembolso_id, monto
        FROM reembolso
        WHERE referencia_externa = %s
    """, (clave_operacion,)).fetchone()

    if existente:
        return {
            "estado": "YA_REEMBOLSADO",
            "reembolso_id": existente["reembolso_id"],
            "monto": str(existente["monto"])
        }

    # Registrar el reembolso en la misma transacción.
    nuevo = conn.execute("""
        INSERT INTO reembolso
            (intento_id, monto, motivo, referencia_externa)
        VALUES (%s, %s, %s, %s)
        RETURNING reembolso_id
    """, (
        intento_id,
        importe,
        motivo,
        clave_operacion
    )).fetchone()

    return {
        "estado": "REEMBOLSO_REGISTRADO",
        "reembolso_id": nuevo["reembolso_id"],
        "monto": str(importe)
    }
