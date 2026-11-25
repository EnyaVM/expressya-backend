import os
import secrets
from pathlib import Path

from app.db import pool
from app.modulos.cuentas import crear_cuenta

ARCHIVO = Path(__file__).resolve().parent.parent / "cuentas_demo.txt"
NORTE = "11111111-1111-1111-1111-111111111111"
SUR = "11111111-1111-1111-1111-111111111112"
CUENTAS = [
    ("admin", "Administración ExpressYa", "ADMINISTRADOR", []),
    ("cliente.demo", "Cliente de demostración", "CLIENTE", []),
    ("picker.norte", "Picker Norte", "INTERNO", [(NORTE, "PICKER")]),
    ("personal.norte", "Personal de tienda Norte", "INTERNO", [(NORTE, "PERSONAL_TIENDA")]),
    ("supervisor.norte", "Supervisor Norte", "INTERNO", [(NORTE, "SUPERVISOR")]),
    ("inspector.norte", "Inspector Norte", "INTERNO", [(NORTE, "INSPECTOR")]),
    ("autorizador.norte", "Autorizador Norte", "INTERNO", [(NORTE, "AUTORIZADOR")]),
    ("responsable.norte", "Responsable Norte", "INTERNO", [(NORTE, "RESPONSABLE")]),
    ("picker.sur", "Picker Sur", "INTERNO", [(SUR, "PICKER")]),
]


def faltantes(conn):
    existentes = {fila[0] for fila in conn.execute("SELECT usuario FROM cuenta")}
    return [c for c in CUENTAS if c[0] not in existentes]


def crear_faltantes():
    nuevas = []
    with pool.connection() as conn:
        for usuario, nombre, tipo, asignaciones in faltantes(conn):
            clave = os.getenv("EXPRESSYA_CLAVE_DEMO") or secrets.token_urlsafe(9)
            crear_cuenta(conn, usuario, nombre, clave, tipo, asignaciones)
            nuevas.append((usuario, clave, tipo))
    with ARCHIVO.open("a", encoding="utf-8") as archivo:
        for usuario, clave, tipo in nuevas:
            archivo.write(f"{usuario:<20} {clave:<16} {tipo}\n")
    return nuevas


if __name__ == "__main__":
    creadas = crear_faltantes()
    if creadas:
        print(f"Cuentas nuevas: {len(creadas)}. Usuarios y contraseñas en {ARCHIVO.name} (no se sube a GitHub).")
    else:
        print(f"Las cuentas de la demo ya existen. Sus contraseñas están en {ARCHIVO.name}.")
