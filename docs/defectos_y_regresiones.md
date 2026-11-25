# Registro de defectos, corrección y regresión

Cada defecto se encontró al construir o al revisar el prototipo. La columna «Regresión» es la prueba automatizada que lo vuelve a detectar si regresa. Todas corren en `python -m pytest -q` y en el CI («Pruebas»).

| ID | Defecto encontrado | Cómo se encontró | Causa | Corrección | Regresión |
|---|---|---|---|---|---|
| DEF-01 | El CI de `main` falló tras un push directo de C4 | Notificación de GitHub Actions | La prueba E2E usaba una fixture que no existía | Prueba reescrita con los escenarios comunes (PR #8) | `test_e2e_c4.py::test_flujo_completo_checkout_pago_faltante_cancelacion_y_reembolso` |
| DEF-02 | Con 20 pagos simultáneos, 13 respondían 500 | Prueba de carga local | El candado de la clave ocupaba una conexión del pool mientras se llamaba a la pasarela | Candado de sesión en una conexión dedicada fuera del pool | `test_c2_pagos.py::test_carrera_dos_pagos_a_la_vez_solo_uno_inicia` |
| DEF-03 | Una caída entre el cobro y el registro perdía la Clave-Operacion y permitía cobrar dos veces | Revisión externa (ChatGPT) | La clave solo vivía en `registro_operacion`, que se escribía al final | Migración `db/03`: la clave y su huella se guardan en el intento antes de cobrar | `test_robustez.py::test_caida_tras_el_cobro_conserva_la_clave_y_no_cobra_otra_vez` |
| DEF-04 | Una misma clave podía usarse en una reserva y en un pago | Revisión externa | Cada módulo buscaba la clave solo en sus tablas | `clave_tomada_por_un_pago` en las operaciones de reserva | `test_idempotencia_reservas.py::test_clave_anterior_con_movimiento_sin_respuesta_no_crea_otro_checkout` |
| DEF-05 | Un evento atascado de una versión anterior nunca avisaba al responsable | Revisión externa | El tope de reintentos solo aplicaba a eventos nuevos | `avisar_eventos_detenidos` | `test_robustez.py::test_evento_atascado_de_la_version_anterior_genera_aviso` |
| DEF-06 | CL-08 permitía separar en líneas `NO_SURTIBLE` y los errores de validación salían como 422 | Comparación con el SDD (AT-3 y catálogo de errores) | Validación incompleta y manejador por defecto de FastAPI | Solo `ASIGNADA` admite separar; validaciones a 400 `SOLICITUD_INVALIDA` | `test_cl08_separacion.py::test_cl08_rechaza_cantidad_excesiva`, `test_validacion_reservas.py` |
| DEF-07 | CL-12 fallaba al registrar quién autorizó el ajuste | Prueba TDD de CL-12 | Se hacía UPDATE de `autorizado_por` sobre el historial de solo inserción | `registrar_movimiento` recibe `autorizado_por` al insertar | `test_cl12_resolucion.py::test_la_resolucion_queda_en_el_historial_con_quien_la_autorizo` |
| DEF-08 | `db/93_datos_c3.sql` no cargaba en una base nueva | Carga como el CI | Literales sin tipo en `UNION ALL` | Conversión `::uuid` | Paso «Cargar la base» del CI |
| DEF-09 | Los precios cambiados en `db/` no aparecían en la página | Reporte de Ian | La demo solo cargaba `db/*.sql` si la base no existía | Catálogo como upsert sincronizado en cada arranque y registro de archivos aplicados | `test_preparar_demo.py` (6 pruebas) |
| DEF-10 | Si se retiraba un producto mientras se pagaba, lo cobrado quedaba arriba del total | Límite que el equipo no aceptó declarar | CL-07 no revisaba líneas retiradas después de iniciar el cobro | Reembolso `PRODUCTO_RETIRADO` por línea retirada al confirmar | `test_cl11_sobrecobro.py` (4 pruebas) |
| DEF-11 | Cualquiera podía operar como otro cliente o empleado mandando `Cliente-Id`/`Usuario-Id`, y la página tenía un selector de personal | Revisión de seguridad al pedir credenciales | La identidad venía del navegador | Sesiones con cookie HttpOnly y CSRF; las cabeceras del navegador se descartan | `test_cuentas.py::test_con_sesion_no_se_puede_suplantar_a_otro_cliente` y 15 más |
| DEF-12 | Cualquiera podía cambiar el resultado de los cobros en la pasarela simulada | Misma revisión | `/simulador/modo` sin permisos | Solo administración, fuera de pruebas | `test_cuentas.py::test_solo_administracion_cambia_la_pasarela_de_prueba` |
| DEF-13 | Al refactorizar CL-02 casi se registraba el 401 de un cliente inexistente, lo que gastaba la clave | Revisión del refactor (ub-mode) | El caso no estaba fijado por ninguna prueba | Se conservó el comportamiento y se agregó la prueba | `test_validacion_reservas.py::test_un_cliente_inexistente_no_gasta_la_clave` |
| DEF-14 | La ruta de CL-08 no coincidía con el contrato (`/separacion` vs `/separaciones`) | Comparación con el OpenAPI 1.1 | Nombre elegido en C3 | Ruta del contrato y la anterior como alias oculto | `test_cl08_separacion.py::test_cl08_responde_en_la_ruta_del_contrato_openapi` |
| DEF-16 | Un RESPONSABLE de la tienda B podía aprobar un pago o resolver un reembolso de la tienda A (contradice RNF-06) | Revisión externa | `_exigir_rol_responsable` validaba el rol sin la tienda | `_exigir_responsable_de` busca la tienda del pedido | `test_permisos_por_tienda.py` (3 pruebas) |
| DEF-18 | Dos cuentas internas (por ejemplo picker y supervisor) no podían estar abiertas en pestañas del mismo navegador: la segunda reemplazaba a la primera | Prueba de Ian en la demo | La cookie se separaba por portal, no por pestaña | Cada pestaña genera su identificador (`X-Ventana`, en `sessionStorage`) y su cookie es `expressya_sesion_<portal>_<ventana>` | `test_cuentas.py::test_dos_cuentas_internas_conviven_en_pestanas_del_mismo_navegador`, `test_una_ventana_con_caracteres_raros_no_abre_sesion` |
| DEF-17 | Entrar al área interna reemplazaba la sesión de la tienda en el mismo navegador | Revisión externa | Una sola cookie para los dos portales | Una cookie por portal, elegida con `X-Portal` | `test_cuentas.py::test_tienda_e_interno_conservan_su_propia_sesion_en_el_mismo_navegador` |
| DEF-15 | CL-14 y CL-15 (daños e inspección) estaban en el diseño y no en el código | Revisión del diseño final contra el repo | Pendiente de C4 | `app/modulos/danos.py` | `test_cl14_cl15_danos.py` (11 pruebas) |

## Defectos que encontraron las herramientas de calidad (ub-mode)

| Herramienta | Hallazgo | Acción | Regresión |
|---|---|---|---|
| crapper | 13 funciones con CRAP ≥ 10 (peor: notificación de pasarela 65.1) | Divididas en funciones con nombre; 0 funciones ≥ 10 | Suite completa antes y después de cada refactor |
| dryer | `cobrar`/`reembolsar` duplicados (1.00) y consultas del portal (0.82) | Función `responder` y tablas de consultas | `test_c2_pagos.py`, `test_portal.py` |
| mutator | Mutantes sobrevivientes en C3 (10) | Pruebas de borde nuevas; código muerto eliminado | `test_cl09_conteo.py`, `test_cl12_resolucion.py` |

El resultado de la corrida de mutación sobre todo el repositorio está en `docs/bitacora_ub_mode.md`.
