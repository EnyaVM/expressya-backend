
def notificar_cancelacion(
    conn,
    pedido_id,
    cantidad,
    motivo,
    importe
):
    pedido = conn.execute("""
        SELECT cliente_id
        FROM pedido
        WHERE pedido_id = %s
    """, (pedido_id,)).fetchone()

    if not pedido:
        raise ValueError("PEDIDO_NO_ENCONTRADO")

    mensaje = (
        f"Se cancelaron {cantidad} unidades de tu pedido "
        f"{pedido_id}. Motivo: {motivo}. "
        f"Importe a reembolsar: ${importe}."
    )

    conn.execute("""
        INSERT INTO aviso
            (destinatario, tipo, fecha)
        VALUES (%s, %s, CURRENT_TIMESTAMP)
    """, (
        pedido["cliente_id"],
        "CANCELACION"
    ))

    return {
        "cliente_id": pedido["cliente_id"],
        "mensaje": mensaje
    }
