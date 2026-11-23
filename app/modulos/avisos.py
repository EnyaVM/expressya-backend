from uuid import NAMESPACE_URL, UUID, uuid5

ESPACIO_AVISOS = uuid5(NAMESPACE_URL, "expressya/avisos")


def clave_de_aviso(tipo: str, origen_tipo: str, origen_id: UUID) -> UUID:
    return uuid5(ESPACIO_AVISOS, f"{tipo}:{origen_tipo}:{origen_id}")


def registrar_aviso(
    conn,
    tipo: str,
    origen_tipo: str,
    origen_id: UUID,
    cliente_id: UUID | None = None,
    usuario_id: UUID | None = None,
    pedido_id: UUID | None = None,
) -> UUID:
    clave = clave_de_aviso(tipo, origen_tipo, origen_id)
    destinatario_tipo = "CLIENTE" if cliente_id is not None else "RESPONSABLE"
    conn.execute(
        """
        INSERT INTO aviso (
            destinatario_tipo, cliente_id, usuario_id, tipo,
            origen_tipo, origen_id, pedido_id, clave
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (clave) DO NOTHING
        """,
        (destinatario_tipo, cliente_id, usuario_id, tipo, origen_tipo, origen_id, pedido_id, clave),
    )
    return clave
