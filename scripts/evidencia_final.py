import platform
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from importlib.metadata import version
from pathlib import Path

import psycopg

from app.db import DATABASE_URL

RAIZ = Path(__file__).resolve().parent.parent
JUNIT = RAIZ / "evidencia" / "junit_final.xml"
SALIDA = RAIZ / "docs" / "matriz_trazabilidad.md"

CASOS = [
    ("AT-01", "Última unidad: dos compradores concurrentes, una sola reserva, sin saldo negativo",
     "RC-01, AT-1, SDD 5.1 (bloqueo de inventario)", "reservas.crear_checkout", "CL-02",
     ["test_checkout.py::test_la_ultima_unidad_la_gana_exactamente_un_cliente",
      "test_idempotencia_reservas.py::test_carrera_misma_clave_un_registro_y_un_solo_efecto"]),
    ("AT-02", "Reserva vigente a los 299 s y vencida a los 300 s; sin compromiso posterior",
     "RN-02, trigger controlar_reserva (TTL 300 s)", "reservas.exigir_reserva_vigente, pedidos.confirmar_compra", "CL-02, CL-07",
     ["test_at02_limite_300s.py::test_at02_a_los_299_s_la_reserva_sigue_vigente",
      "test_at02_limite_300s.py::test_at02_a_los_300_s_la_reserva_ya_vencio",
      "test_at02_limite_300s.py::test_at02_una_aprobacion_a_los_300_s_no_compromete_y_se_reembolsa"]),
    ("AT-03", "Pago rechazado: sin pedido pagado ni retención indefinida",
     "CL-04, RT-01", "pagos._cobrar, reservas.expirar_vencidas", "CL-04",
     ["test_c2_pagos.py::test_pago_rechazado_deja_la_reserva_activa_y_permite_reintentar",
      "test_ciclos.py::test_el_ciclo_de_vencimiento_expira_una_reserva_vencida"]),
    ("AT-04", "Pago confirmado después del TTL: no revive la reserva; reembolso",
     "CL-07 caso B, CL-16", "pedidos._reembolsar_pago_tardio", "CL-07",
     ["test_c2_confirmacion.py::test_caso_b_pago_tardio_reembolsa_el_100_por_ciento_sin_revivir_la_reserva",
      "test_c2_confirmacion.py::test_carrera_pago_contra_vencimiento_gana_una_sola_sentencia"]),
    ("AT-05", "Callback duplicado: no duplica cargo, compromiso ni reembolso",
     "CL-05, notificacion_pasarela (PK objeto+referencia)", "pagos.aplicar_notificacion", "CL-05",
     ["test_c2_pagos.py::test_notificacion_repetida_no_cambia_nada",
      "test_c2_confirmacion.py::test_evento_repetido_no_duplica_el_efecto",
      "test_c2_confirmacion.py::test_cobro_duplicado_se_reembolsa_y_el_inventario_se_compromete_una_sola_vez"]),
    ("AT-06", "Timeout financiero: estado desconocido hasta conciliar, sin reintento ciego",
     "CL-04 (202 EN_VERIFICACION), CL-06", "pagos._cerrar_intento, pagos.recuperar_intentos_iniciados", "CL-04, CL-06",
     ["test_c2_pagos.py::test_sin_respuesta_de_la_pasarela_responde_202_en_verificacion",
      "test_c2_recuperacion.py::test_repetir_la_peticion_por_timeout_no_duplica_el_cobro",
      "test_c2_recuperacion.py::test_un_intento_iniciado_viejo_pasa_a_en_verificacion_sin_volver_a_cobrar"]),
    ("AT-07", "Existencia lógica 10, física 8: incidencia y disponibilidad incierta bloqueada",
     "CL-09, CL-12, P4/P8 (unidades en revisión)", "faltantes.registrar_conteo, faltantes.resolver_discrepancia", "CL-09, CL-12",
     ["test_cl09_conteo.py::test_at07_diez_registradas_ocho_contadas_deja_dos_en_revision",
      "test_cl12_resolucion.py::test_at07_resolver_la_diferencia_ajusta_existencias_y_libera_lo_que_estaba_en_revision"]),
    ("AT-08", "Cinco líneas, una faltante: las otras cuatro continúan",
     "CL-10, D-05", "faltantes.reportar_faltante, faltantes.repartir", "CL-10, CL-12",
     ["test_cl10_faltante.py::test_at08_las_otras_lineas_se_siguen_separando_y_la_faltante_no",
      "test_cl12_resolucion.py::test_at08_cinco_lineas_una_faltante_cancela_solo_lo_que_falta_y_las_otras_continuan"]),
    ("AT-09", "Reembolso parcial repetido: efecto económico aplicado una vez",
     "CL-13 interno, CL-16, uq cancelacion_id", "cancelaciones.cancelar_cantidad_por_faltante", "CL-12, CL-13",
     ["test_cancelaciones.py::test_aplicar_dos_veces_el_mismo_faltante_no_duplica_el_reembolso",
      "test_cl12_resolucion.py::test_resolver_dos_veces_da_409_y_la_misma_clave_devuelve_la_respuesta_guardada"]),
    ("AT-10", "Caída tras persistir y antes de notificar: estado y evento recuperables",
     "EV-01 en la misma transacción, recuperación", "pedidos.procesar_eventos_pendientes, pagos.recuperar_reembolsos_sin_enviar", "CL-07, CL-16",
     ["test_c2_recuperacion.py::test_un_evento_sin_procesar_se_reprocesa_una_sola_vez",
      "test_robustez.py::test_caida_tras_el_cobro_conserva_la_clave_y_no_cobra_otra_vez",
      "test_robustez.py::test_caida_antes_de_enviar_el_reembolso_se_recupera"]),
    ("C3-HIST", "Historial: quién, qué, cuándo, por qué y cómo afectó al pedido",
     "movimiento_inventario de solo inserción, v_reconstruccion_saldos", "faltantes.registrar_movimiento", "CL-09 a CL-12",
     ["test_cl12_resolucion.py::test_la_resolucion_queda_en_el_historial_con_quien_la_autorizo",
      "test_reservas_cierre.py::test_el_saldo_se_reconstruye_desde_los_movimientos"]),
    ("C3-SEP", "Separación sin sobreseparar bajo concurrencia",
     "CL-08, AT-3 del SDD", "pedidos.separar_cantidad", "CL-08",
     ["test_cl08_separacion.py::test_cl08_concurrencia_no_sobresepara",
      "test_cl08_separacion.py::test_cl08_rechaza_cantidad_excesiva"]),
    ("C3-CANC", "Cancelación del cliente antes de preparar, con reembolso y aviso",
     "CL-13, PV-08", "cancelaciones.cancelar_pedido_cliente", "CL-13",
     ["test_portal.py::test_cancelacion_reembolso_y_aviso_visibles",
      "test_portal.py::test_cl08_real_impide_cancelacion_y_se_refleja_en_portal"]),
    ("C3-RETIRO", "Producto retirado durante el pago: se reembolsa lo retirado",
     "CL-11, CL-07, uq_reembolso_linea_quitada", "pedidos.reembolsar_productos_retirados", "CL-07, CL-11",
     ["test_cl11_sobrecobro.py::test_producto_retirado_durante_el_pago_se_reembolsa_al_confirmar",
      "test_cl11_sobrecobro.py::test_dos_productos_retirados_reembolsan_cada_uno_una_vez"]),
    ("C3-DANO", "Daño, inspección y reincorporación con dos personas distintas",
     "CL-14, CL-15, D-09", "danos.reportar_dano, danos.resolver_inspeccion", "CL-14, CL-15",
     ["test_cl14_cl15_danos.py::test_un_dano_que_deja_deficit_bloquea_y_abre_discrepancia_d05",
      "test_cl14_cl15_danos.py::test_at07_reincorporar_lo_apto_exige_una_segunda_persona",
      "test_cl14_cl15_danos.py::test_quien_inspecciona_no_puede_autorizar_su_propia_inspeccion"]),
    ("C4-E2E", "Flujo completo: checkout, pago, faltante, cancelación parcial y reembolso",
     "C1 a C3 integrados", "módulos M01 a M06", "CL-02 a CL-16",
     ["test_e2e_c4.py::test_flujo_completo_checkout_pago_faltante_cancelacion_y_reembolso"]),
    ("SEG-01", "Identidad por sesión: nadie opera como otro cliente o empleado",
     "RNF-06 (RBAC), securitySchemes", "seguridad.Autenticacion", "Todos",
     ["test_cuentas.py::test_sin_sesion_un_id_de_cliente_inventado_no_sirve",
      "test_cuentas.py::test_con_sesion_no_se_puede_suplantar_a_otro_cliente",
      "test_cuentas.py::test_administracion_consulta_todo_pero_no_opera"]),
    ("SEG-02", "Perfiles por tienda y administración de cuentas auditada",
     "asignacion_rol, auditoria_cuenta", "cuentas.dar_de_alta, cuentas.cambiar_acceso", "Cuentas",
     ["test_cuentas.py::test_administracion_da_de_alta_personal_con_perfiles_y_queda_auditado",
      "test_cuentas.py::test_desactivar_una_cuenta_corta_su_sesion_abierta"]),
]


def correr_pruebas() -> None:
    subprocess.run([sys.executable, "scripts/reconstruir_base.py"], cwd=RAIZ, check=True)
    subprocess.run([sys.executable, "-m", "pytest", "-q", f"--junitxml={JUNIT}"], cwd=RAIZ)


def resultados() -> dict[str, str]:
    salida = {}
    for caso in ET.parse(JUNIT).getroot().iter("testcase"):
        archivo = caso.get("classname").split(".")[-1] + ".py"
        nombre = caso.get("name").split("[")[0]
        fallo = any(h.tag in ("failure", "error") for h in caso)
        previo = salida.get(f"{archivo}::{nombre}", "PASA")
        salida[f"{archivo}::{nombre}"] = "FALLA" if fallo or previo == "FALLA" else "PASA"
    return salida


def resultado_de(pruebas: list[str], obtenidos: dict[str, str]) -> str:
    estados = [obtenidos.get(p, "NO EJECUTADA") for p in pruebas]
    return "PASA" if all(e == "PASA" for e in estados) else "FALLA: " + ", ".join(
        p for p, e in zip(pruebas, estados) if e != "PASA")


def ambiente() -> list[str]:
    with psycopg.connect(DATABASE_URL) as conn:
        postgres = conn.execute("SHOW server_version").fetchone()[0]
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=RAIZ, capture_output=True, text=True).stdout.strip()
    return [f"- Commit: `{commit}`", f"- Fecha: {datetime.now().isoformat(timespec='seconds')}",
            f"- Python {platform.python_version()}, PostgreSQL {postgres}, FastAPI {version('fastapi')}, "
            f"psycopg {version('psycopg')}, pytest {version('pytest')}"]


def conteo_junit() -> tuple[int, int]:
    raiz = ET.parse(JUNIT).getroot()
    suite = raiz if raiz.tag == "testsuite" else raiz.find("testsuite")
    return int(suite.get("tests")), int(suite.get("failures")) + int(suite.get("errors"))


def generar(obtenidos: dict[str, str]) -> str:
    casos, fallas = conteo_junit()
    filas = [f"| {i} | {req} | {dis} | `{cod}` ({cl}) | {'<br>'.join(f'`{p}`' for p in pr)} | Automatizada | {resultado_de(pr, obtenidos)} |"
             for i, req, dis, cod, cl, pr in CASOS]
    return "\n".join([
        "# Matriz Requisito → Diseño → Código → Prueba → Resultado",
        "",
        "Generada por `python -m scripts.evidencia_final` sobre una base reconstruida desde `db/*.sql`.",
        "",
        *ambiente(),
        f"- Suite completa: {casos - fallas} de {casos} casos pasan ({fallas} fallan)",
        "",
        "| ID | Requisito | Diseño | Código (contrato) | Pruebas | Tipo | Resultado |",
        "|---|---|---|---|---|---|---|",
        *filas,
        "",
        "Además de lo automatizado, el recorrido en navegador (sección 9 del README) se hace a mano en la demostración.",
        "",
    ])


if __name__ == "__main__":
    correr_pruebas()
    SALIDA.write_text(generar(resultados()), encoding="utf-8")
    print(f"Matriz escrita en {SALIDA.relative_to(RAIZ)}")
