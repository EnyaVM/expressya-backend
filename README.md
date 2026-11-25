# ExpressYa · backend (Equipo 03)

Backend de ExpressYa con el stack del SDD v1.1: Python 3.12, FastAPI, psycopg 3, PostgreSQL 16 y pytest. Incluye la consulta de disponibilidad (CL-01) y el núcleo persistente de reservas (C1): checkout con reserva de 300 s (CL-02), liberación (CL-03), expiración, historial de movimientos y reconstrucción de saldos.

## Interfaz para la demostración

En la Mac, abre Docker Desktop y ejecuta **Iniciar ExpressYa.command** con doble clic. Abre **http://127.0.0.1:8001** (tienda) o **http://127.0.0.1:8001/interno/** (personal) y deja la terminal encendida.

Cada vez que se abre, el lanzador:
1. Crea la base de la demo `expressya_demo_20261123` si no existe; si existe, conserva pedidos y movimientos.
2. Aplica los archivos nuevos de `db/` una sola vez (lleva el registro en `expressya.archivo_cargado`) y sincroniza el catálogo de `db/91_catalogo_c1.sql`. **Para cambiar un precio basta con editarlo ahí y volver a abrir la demo.**
3. Crea las cuentas de la demo que falten y guarda sus contraseñas en `cuentas_demo.txt` (ese archivo no se sube a GitHub).

| Cuenta | Portal | Puede |
|---|---|---|
| `cliente.demo` (o cualquier cuenta creada con «Crear cuenta») | Tienda | Reservar, pagar, liberar, cancelar y ver solo sus pedidos |
| `picker.norte` | Interno | Separar (CL-08), contar (CL-09) y reportar faltantes (CL-10) en Dark Store Norte |
| `personal.norte` | Interno | Reportar faltantes (CL-10) y daños (CL-14) |
| `supervisor.norte` | Interno | Contar y resolver discrepancias con D-05 (CL-12) |
| `inspector.norte` / `autorizador.norte` | Interno | Inspeccionar daños y autorizar reincorporación o desecho (CL-15), siempre dos personas distintas |
| `responsable.norte` | Interno | Verificar pagos y reembolsos con evidencia (CL-06) y reintentar reembolsos (CL-17) |
| `picker.sur` | Interno | Igual que el picker, pero solo en Dark Store Sur (sirve para mostrar que no ve Norte) |
| `admin` | Interno | Ver todas las tiendas, dar de alta cuentas y perfiles, activar o desactivar accesos, ver la auditoría y configurar la pasarela de prueba. **No opera** pedidos ni inventario |

Los pagos usan la pasarela de prueba del proyecto y no hacen cargos reales. La identidad sale de la sesión del servidor: la página ya no manda `Cliente-Id` ni `Usuario-Id`, y si alguien los inventa se descartan (ver sección 9).

## Qué necesitas instalar (una sola vez)

- Python 3.12: https://www.python.org/downloads/
- Docker Desktop (para tener PostgreSQL 16 sin instalarlo a mano): https://www.docker.com/products/docker-desktop/
- Visual Studio Code (opcional, para editar).

## 1. Levantar la base de datos

En la Terminal, dentro de esta carpeta:

```bash
docker run --name expressya-db -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=expressya_db -p 5432:5432 -d postgres:16
```

Espera unos segundos, prepara Python (paso 2) y carga todos los archivos de `db/` en orden:

```bash
python scripts/reconstruir_base.py
```

Con eso quedan 5 Dark Stores, 5 productos de ropa con precio, el inventario inicial con su movimiento, los usuarios de prueba y las tablas de cuentas. Para crear las cuentas de la demo en esta base: `python -m scripts.crear_cuentas_demo`.

Para apagarla: `docker stop expressya-db`. Para volver a prenderla: `docker start expressya-db`.

Para empezar de cero, vuelve a ejecutar `python scripts/reconstruir_base.py`: borra el esquema y carga todo otra vez.

## 2. Preparar Python

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Cada vez que abras una Terminal nueva, vuelve a ejecutar `source .venv/bin/activate`.

## 3. Correr la aplicación

```bash
uvicorn app.main:app --reload
```

- Página: http://localhost:8000
- Documentación automática de la API (Swagger): http://localhost:8000/docs
- Prueba de conexión con la base: http://localhost:8000/v1/salud

Entra con una cuenta creada con `python -m scripts.crear_cuentas_demo` (ver «Interfaz para la demostración»). En Swagger, haz `POST /v1/auth/login` con `"entregar_token": true` y usa el token como `Authorization: Bearer ...`.

Al arrancar, la aplicación inicia un proceso que cada 5 segundos expira las reservas cuyo plazo de 300 s ya se cumplió, procesa eventos internos EV-01 pendientes y recuperan intentos de pago huérfanos en estado INICIADO.

## 4. Correr las pruebas

Con la base prendida:

```bash
python -m pytest -q
```

Las pruebas crean sus propias tiendas, productos y clientes, así que se pueden repetir sin limpiar nada (el historial de movimientos es de solo inserción y no se borra). `tests/conftest.py` activa `EXPRESSYA_ENCABEZADOS_DE_PRUEBA=1` para que las pruebas de contratos puedan mandar `Cliente-Id` y `Usuario-Id`; las pruebas de `tests/test_cuentas.py` lo apagan y comprueban que, fuera de pruebas, solo cuenta la sesión.

Para generar la matriz de trazabilidad con el resultado de esta misma corrida, el commit y las versiones:

```bash
python -m scripts.evidencia_final
```

Escribe `docs/matriz_trazabilidad.md` y `evidencia/junit_final.xml`.

## 5. Evidencia del desafío C1

Con la base prendida y el entorno activado:

```bash
bash scripts/evidencia_c1.sh
```

El script corre las pruebas de C1 con detalle, repite 20 veces las pruebas de carrera y guarda todo en `evidencia/c1_resultados.txt` (fecha, versión de Python y resultado de cada prueba). Se puede cambiar el número de repeticiones con `REPETICIONES=50 bash scripts/evidencia_c1.sh`.

Qué demuestra cada prueba del desafío:

| Requisito del desafío | Prueba | Archivo |
|---|---|---|
| Disponibilidad por tienda | `test_hay_suficiente_y_devuelve_el_precio` y las de agotado, inexistente y repetido | `tests/test_checkout.py` |
| Reserva con vencimiento exacto de 300 s | `test_la_reserva_vence_exactamente_a_los_300_segundos` | `tests/test_checkout.py` |
| Dos solicitudes por la última unidad: gana exactamente una | `test_la_ultima_unidad_la_gana_exactamente_un_cliente` (2 y 8 clientes a la vez) | `tests/test_checkout.py` |
| Liberar una reserva | `test_liberar_devuelve_las_unidades_y_deja_su_movimiento` y las de otro cliente y doble liberación | `tests/test_reservas_cierre.py` |
| Proceso de expiración | `test_una_reserva_vencida_expira_y_regresa_las_unidades` y `test_el_proceso_de_expiracion_cierra_solo_las_vencidas` | `tests/test_reservas_cierre.py` |
| Comprobación al usar una reserva | `test_al_usar_una_reserva_se_comprueba_que_siga_vigente` | `tests/test_reservas_cierre.py` |
| Liberar y vencer al mismo tiempo: un solo cierre | `test_liberar_y_expirar_a_la_vez_cierra_la_reserva_una_sola_vez` | `tests/test_reservas_cierre.py` |
| Movimientos auditables y saldo reconstruible | `test_la_reserva_deja_su_movimiento_auditable` y `test_el_saldo_se_reconstruye_desde_los_movimientos` | `tests/test_checkout.py`, `tests/test_reservas_cierre.py` |

Cómo está resuelto (diseño SDD v1.1):

- **Reserva (AT-1):** en una sola transacción se bloquea al cliente, luego las filas de inventario por producto en orden, y se comprueba que cada producto esté habilitado y tenga disponibles suficientes. Quien llega segundo ve la unidad ya reservada y recibe `409 SIN_DISPONIBLE`.
- **Plazo de 300 s:** lo fija un trigger de la base con el reloj del servidor; el plazo no se puede modificar.
- **Salida de ACTIVA (AT-2):** liberar y expirar hacen el mismo `UPDATE reserva … WHERE estado = 'ACTIVA'` bajo el bloqueo del pedido, y la expiración exige `clock_timestamp() >= t_vence`. Solo una operación gana; la otra obtiene 0 filas.
- **Historial:** cada reserva, liberación y expiración inserta un movimiento (`RESERVA`, `LIBERACION`, `EXPIRACION`) con el detalle del saldo `R`. El saldo guardado de E y el de R se pueden reconstruir sumando `movimiento_saldo`.

### Reintentos de checkout y cierre (CL-02 y CL-03)

Envía el UUID del cliente en `Cliente-Id` y un UUID v4 nuevo en `Clave-Operacion` para cada operación nueva. Si se pierde la respuesta, repite la solicitud con el mismo cliente, ruta, cuerpo y clave. La API devuelve el código HTTP y el cuerpo originales con `Resultado-Previo: true`, incluso si la reserva ya se cerró. Un segundo cierre con otra clave devuelve `409 ESTADO_INCOMPATIBLE`. Reutilizar la clave para otro contenido, cliente u operación devuelve `409 CONFLICTO_DE_CLAVE`.

PostgreSQL bloquea la clave y registra el resultado en `registro_operacion`. Si la operación produce un rechazo de negocio, un punto de restauración (savepoint) deshace sus efectos. La transacción exterior guarda el rechazo antes de liberar la clave. Dos solicitudes simultáneas recuperan así una única decisión. Si cambia el inventario y el cliente quiere volver a intentar la compra, debe iniciar una operación nueva.

La reproducción exacta cubre las respuestas guardadas desde esta versión. Si una clave anterior ya aparece en el historial de movimientos pero no tiene una respuesta guardada, la API devuelve `409 CONFLICTO_DE_CLAVE`. Los cierres de la versión anterior generaban una clave interna, por lo que su respuesta original no se puede recuperar con la cabecera del cliente.

Mientras se use la cabecera temporal, un `Cliente-Id` ausente o mal formado devuelve `401 NO_AUTENTICADO`. Los errores de formato del cuerpo, la ruta o la clave devuelven `400 SOLICITUD_INVALIDA`. El error `SIN_DISPONIBLE` identifica el producto afectado sin mostrar el saldo de la tienda.

Las pruebas de estas reglas están en `tests/test_idempotencia_reservas.py`, `tests/test_validacion_reservas.py` y `tests/test_reservas_cierre.py`.

## 6. Evidencia del desafío C2
  Con la base prendida y el entorno activado (Bloque B):

  ```bash
    python scripts/reconstruir_base.py
    python -m pytest -q
    python scripts/evidencia_c2.py
  ```

  El script reconstruye la base desde cero, ejecuta toda la suite (C1 y C2), repite 20 veces las pruebas de carrera y guarda el resultado en 'evidencia/c2_resultados.txt'.

Qué demuestra cada prueba del desafío C2:

| Requisito del desafío C2 | Prueba | Archivo |
|---|---|---|
| Pago aprobado a tiempo confirma pedido y compromete inventario | `test_pago_aprobado_a_tiempo_confirma_el_pedido`, `test_caso_a_compromete_la_reserva_y_deja_movimientos_compromiso` | `tests/test_c2_pagos.py`, `tests/test_c2_confirmacion.py` |
| Pago rechazado deja reserva activa y permite reintentar | `test_pago_rechazado_deja_la_reserva_activa_y_permite_reintentar` | `tests/test_c2_pagos.py` |
| Sin respuesta de la pasarela responde `202 EN_VERIFICACION` sin recobrar | `test_sin_respuesta_de_la_pasarela_responde_202_en_verificacion`, `test_con_intento_abierto_otro_pago_da_intento_abierto` | `tests/test_c2_pagos.py` |
| Dos solicitudes de pago concurrentes: solo inicia una | `test_carrera_dos_pagos_a_la_vez_solo_uno_inicia` (2 y 8 hilos) | `tests/test_c2_pagos.py` |
| Pago tardío reembolsado al 100 % sin revivir la reserva ni pasar a `RECHAZADO` | `test_caso_b_pago_tardio_reembolsa_el_100_por_ciento_sin_revivir_la_reserva`, `test_pago_tardio_con_la_reserva_ya_expirada_por_el_proceso` | `tests/test_c2_confirmacion.py` |
| Carrera pago vs. vencimiento: un solo desenlace consistente | `test_carrera_pago_contra_vencimiento_gana_una_sola_sentencia` | `tests/test_c2_confirmacion.py` |
| Cobro duplicado reembolsado sin comprometer stock dos veces | `test_cobro_duplicado_se_reembolsa_y_el_inventario_se_compromete_una_sola_vez` | `tests/test_c2_confirmacion.py` |
| Callback duplicado y evento repetido sin efecto doble | `test_notificacion_repetida_no_cambia_nada`, `test_evento_repetido_no_duplica_el_efecto` | `tests/test_c2_pagos.py`, `tests/test_c2_confirmacion.py` |
| Idempotencia con `Clave-Operacion` y `CONFLICTO_DE_CLAVE` | `test_misma_clave_devuelve_la_respuesta_guardada`, `test_misma_clave_con_otro_monto_da_conflicto_de_clave` | `tests/test_c2_pagos.py` |
| Reembolso, cola de espera y tope máximo | `test_reembolso_confirmado_deja_el_intento_reembolsado_total`, `test_reembolso_sin_respuesta_queda_en_verificacion_sin_reenviar_y_el_segundo_espera`, `test_el_reembolso_nunca_supera_lo_cobrado` | `tests/test_c2_reembolsos.py` |
| Revisión manual con evidencia (`CL-06`) y reintento (`CL-17`) | `test_cl06_resuelve_un_cobro_en_verificacion_con_evidencia`, `test_cl06_resuelve_un_reembolso_en_verificacion`, `test_cl06_sin_rol_responsable_da_403`, `test_cl17_solo_reintenta_desde_reembolso_fallido` | `tests/test_c2_reembolsos.py` |
| Recuperación tras caída sin duplicar cobros ni efectos | `test_un_intento_iniciado_viejo_pasa_a_en_verificacion_sin_volver_a_cobrar`, `test_un_evento_sin_procesar_se_reprocesa_una_sola_vez`, `test_repetir_la_peticion_por_timeout_no_duplica_el_cobro` | `tests/test_c2_recuperacion.py` |

Decisiones `PROPUESTA` del equipo (SDD v1.1):
- **Monto del intento de pago (CL-04):** debe coincidir exactamente con el `total_vigente` del pedido; si difiere, responde `400 SOLICITUD_INVALIDA`.
- **Clave idempotente en notificaciones de pasarela (CL-05):** se deriva de `(objeto, objeto_id, referencia_externa)` y se registra tanto en `notificacion_pasarela` como en `registro_operacion`.
- **Reembolso por cobro duplicado (ADV-001):** si un segundo intento `APROBADO` llega cuando el pedido ya está `CONFIRMADO` o `CANCELADO`, se dispara automáticamente un reembolso por el 100 % del intento duplicado (`COBRO_DUPLICADO`).
- **Plazo de espera de pasarela:** 30 segundos para mover intentos `INICIADO` huérfanos a `EN_VERIFICACION` durante la recuperación tras caída.
- **Autenticación temporal:** `Cliente-Id` y `Usuario-Id` se reciben como cabeceras temporales hasta integrar autenticación completa.

Rutas de control del simulador de pasarela (rutas internas de prueba, fuera de `CL-xx`):
- `GET /simulador/modo`: consulta el modo actual configurado (`cobro` y `reembolso`).
- `POST /simulador/modo`: configura dinámicamente el comportamiento (`cobro`: `APROBAR` | `RECHAZAR` | `NO_RESPONDER`; `reembolso`: `CONFIRMAR` | `FALLAR` | `NO_RESPONDER`).

## 7. Protocolo de recuperación

Regla general: **ante la duda, nunca se cobra ni se devuelve dos veces.** Lo que no se puede confirmar pasa a verificación y lo resuelve una persona con evidencia.

### Lo que hace el servidor solo (cada 5 segundos, `app/main.py`)

| # | Situación tras una caída o un fallo | Qué hace el servidor | Nunca hace | Prueba |
|---|---|---|---|---|
| 1 | Reserva `ACTIVA` que ya pasó sus 300 s | La pasa a `EXPIRADA` y libera R | Revivirla | `test_el_proceso_de_expiracion_cierra_solo_las_vencidas` |
| 2 | Cobro `INICIADO` sin respuesta por 30 s o más | Lo pasa a `EN_VERIFICACION` | Volver a cobrar | `test_un_intento_iniciado_viejo_pasa_a_en_verificacion_sin_volver_a_cobrar` |
| 3 | Evento `EV-01` sin procesar (pago aprobado sin confirmar) | Lo reprocesa una vez; si falla, sigue con los demás | Dejar que un evento trabe a los otros | `test_un_evento_que_falla_no_bloquea_a_los_demas` |
| 4 | Evento que falla 5 veces | Deja de reintentarlo y genera el aviso `EVENTO_SIN_PROCESAR` al responsable | Reintentarlo sin fin | `test_un_evento_que_falla_no_bloquea_a_los_demas` |
| 5 | Reembolso solicitado que nunca se envió (`fecha_envio` vacía) | Lo envía | Enviar uno que ya salió | `test_caida_antes_de_enviar_el_reembolso_se_recupera` |
| 6 | Reembolso enviado sin respuesta por 30 s o más | Lo pasa a `REEMBOLSO_EN_VERIFICACION` | Reenviarlo | `test_reembolso_enviado_sin_respuesta_por_caida_pasa_a_verificacion_sin_reenviar` |

### Lo que hace el cliente al reintentar

- Si no recibió respuesta, repite **la misma operación con la misma `Clave-Operacion`**: recibe la respuesta original con `Resultado-Previo: true` y sin efecto doble (`registro_operacion`).
- Para una operación nueva siempre genera una clave nueva. Si reutiliza una clave con otros datos, otro pedido u otro cliente, recibe `409 CONFLICTO_DE_CLAVE` y no se cobra nada (`test_misma_clave_en_dos_pedidos_simultaneos_cobra_una_sola_vez`, `test_otro_cliente_no_recibe_la_respuesta_guardada_de_un_pago`).
- Si repite un pago mientras el primero sigue en curso, espera y recibe el mismo resultado con un solo cobro (`test_misma_clave_mismo_pedido_simultaneo_devuelve_el_mismo_resultado`).

### Lo que hace el responsable (manual)

1. Revisar los pendientes:

   ```sql
   SET search_path TO expressya;
   SELECT intento_id, estado, fecha_inicio FROM intento_pago
    WHERE estado IN ('EN_VERIFICACION', 'REEMBOLSO_EN_VERIFICACION', 'REEMBOLSO_FALLIDO');
   SELECT evento_id, intento_id, intentos_procesamiento FROM evento_interno
    WHERE NOT procesado AND intentos_procesamiento >= 5;
   SELECT tipo, origen_tipo, origen_id, fecha FROM aviso
    WHERE destinatario_tipo = 'RESPONSABLE' AND entregado_en IS NULL;
   ```

2. Cobro o reembolso en verificación: consultar a la pasarela y resolver con evidencia en `CL-06` (`POST /v1/intentos-pago/{id}/verificacion` o `POST /v1/reembolsos/{id}/verificacion`).
3. Reembolso fallido confirmado: reintentar con `CL-17` (`POST /v1/reembolsos/{id}/reintentos`).
4. Evento detenido tras 5 fallos: corregir la causa y volver a habilitarlo; el servidor lo retoma en el siguiente ciclo:

   ```sql
   UPDATE expressya.evento_interno SET intentos_procesamiento = 0 WHERE evento_id = '<evento_id>';
   ```

### Por qué una misma clave no produce dos cobros

- `db/03_clave_intento_pago.sql` guarda la `Clave-Operacion` y la huella en `intento_pago` **antes** de llamar a la pasarela. Si el proceso se cae después del cobro y antes de guardar la respuesta, la clave sigue tomada: otro pedido con esa clave recibe `409 CONFLICTO_DE_CLAVE` y repetir el mismo pago devuelve `202 EN_VERIFICACION` con el mismo intento, sin cobrar otra vez (`test_caida_tras_el_cobro_conserva_la_clave_y_no_cobra_otra_vez`).
- Checkout, cierre, cancelación y verificaciones revisan esa columna: una clave usada por un pago no se acepta en otra operación (`test_checkout_con_la_clave_de_un_pago_en_curso_da_conflicto`, `test_cancelar_con_la_clave_de_un_pago_da_conflicto`).
- Mientras un pago está en curso, otra solicitud con la misma clave espera a que termine (cerrojo en una conexión aparte del pool).
- Un evento que ya estaba detenido antes de esta versión también genera su aviso (`test_evento_atascado_de_la_version_anterior_genera_aviso`).
- Lo que esto no cubre: una restauración de la base desde un respaldo anterior al cobro. Para ese caso aplica la regla del diseño (8.1): comparar referencias de la pasarela antes de cobrar o devolver otra vez.

Los 30 s de espera, el tope de 5 intentos por evento, la clave guardada en `intento_pago` y el cerrojo por `Clave-Operacion` son decisiones `PROPUESTA` del equipo, no requisitos del cliente.

## 8. Evidencia del desafío C3

Ruta de cada operación (cabeceras `Clave-Operacion` y `Usuario-Id`; repetir la misma clave devuelve la respuesta guardada):

| Contrato | Ruta | Perfil en la tienda |
|---|---|---|
| CL-09 Conteo físico | `POST /v1/darkstores/{darkstore_id}/productos/{producto_id}/conteos` `{cantidad_contada, fecha_corte}` | Picker o Supervisor |
| CL-10 Reporte de faltante | `POST /v1/lineas-pedido/{linea_id}/faltantes` `{evidencia}` | Picker o Personal de tienda |
| CL-11 Retiro de reservas activas | interno, en la misma transacción que detecta el faltante | — |
| CL-12 Resolución (D-05) | `POST /v1/discrepancias/{discrepancia_id}/resolucion` `{conteo_id, unidades_validadas, evidencia, motivo}` | Supervisor |

| Requisito del desafío C3 / escenario | Prueba | Archivo |
|---|---|---|
| AT-07: 10 registradas, 8 contadas; incidencia registrada y disponibilidad incierta bloqueada | `test_at07_diez_registradas_ocho_contadas_deja_dos_en_revision`, `test_at07_resolver_la_diferencia_ajusta_existencias_y_libera_lo_que_estaba_en_revision` | `tests/test_cl09_conteo.py`, `tests/test_cl12_resolucion.py` |
| AT-08: cinco líneas, una faltante; las otras cuatro continúan | `test_at08_las_otras_lineas_se_siguen_separando_y_la_faltante_no`, `test_at08_cinco_lineas_una_faltante_cancela_solo_lo_que_falta_y_las_otras_continuan` | `tests/test_cl10_faltante.py`, `tests/test_cl12_resolucion.py` |
| Cancelación parcial y reembolso de lo faltante | `test_d05_reparte_por_orden_de_compromiso_y_cancela_al_ultimo`, `test_las_unidades_ya_separadas_se_quedan_con_su_pedido`, `test_faltante_total_cancela_la_linea_y_el_pedido_de_un_solo_producto` | `tests/test_cl12_resolucion.py` |
| AT-09: reembolso parcial repetido aplicado una sola vez | `test_resolver_dos_veces_da_409_y_la_misma_clave_devuelve_la_respuesta_guardada`, `test_aplicar_dos_veces_el_mismo_faltante_no_duplica_el_reembolso` | `tests/test_cl12_resolucion.py`, `tests/test_cancelaciones.py` |
| Producto retirado mientras se paga: se reembolsa lo retirado al confirmar | `test_producto_retirado_durante_el_pago_se_reembolsa_al_confirmar`, `test_dos_productos_retirados_reembolsan_cada_uno_una_vez`, `test_si_se_retira_todo_durante_el_pago_se_reembolsa_completo` | `tests/test_cl11_sobrecobro.py` |
| Historial: quién, qué, cuándo, por qué y cómo afectó al pedido | `test_la_resolucion_queda_en_el_historial_con_quien_la_autorizo`; `v_reconstruccion_saldos` cuadra en cada prueba (`historial_cuadra`) | `tests/test_cl12_resolucion.py` |
| Perfiles por tienda (RBAC) | `test_sin_rol_de_conteo_en_la_tienda_responde_403`, `test_personal_de_tienda_puede_reportar_y_responsable_no`, `test_solo_el_supervisor_de_la_tienda_resuelve` | los tres archivos de C3 |

Demo desde la página en Dark Store Norte, con la Sudadera (E = 10):
1. `cliente.demo` reserva 3 unidades: `E=10 R=3 D=7`.
2. `picker.norte` registra un conteo de 8 en «Conteos y discrepancias»: `E=8 N=2 D=5` y discrepancia `EN_VALIDACION`.
3. `supervisor.norte` resuelve con `unidades_validadas = 9`: `E=9 N=0 D=6`.
4. «Ver movimientos» del producto muestra el ajuste inicial, la reserva, la discrepancia (`en_revision`, quién contó) y la resolución (`ajuste_conteo`, quién autorizó).

Decisiones `PROPUESTA` del equipo (trazadas a D-05, P4 y P8):
- **Unidades en revisión:** si el conteo es menor que E, la diferencia pasa de E a N «en revisión» (P4, P8); si además no cubre reservas y compromisos, el producto se bloquea, las líneas pagadas pasan a `NO_SURTIBLE` y se retira de las reservas activas (D-05 pasos 1 y 2).
- **Una sola regla de resolución:** `unidades_validadas` son las unidades físicas confirmadas; E queda en ese valor y las N en revisión se liberan, para faltante, diferencia o sobrante.
- **Reparto D-05:** lo separado se conserva; el resto se asigna por `fecha_compromiso`, del más antiguo al más reciente; lo que no alcanza se cancela con su reembolso (pendiente de validación del cliente: orden de prioridad).
- **Una discrepancia abierta por producto:** un conteo nuevo se agrega como evidencia; un reporte de faltante sobre una diferencia abierta la convierte en faltante.
- **CL-11 síncrono:** el retiro de reservas ocurre en la misma transacción y el EV-03 se registra ya procesado.
- **Rol de resolución:** `SUPERVISOR` (OpenAPI x-roles; rol candidato pendiente de validación).

- **Producto retirado durante el pago:** si CL-11 retira un producto de una reserva cuyo pago ya inició, al confirmarse el cobro (CL-07) se genera un reembolso por cada línea retirada (`PRODUCTO_RETIRADO`, con `reserva_origen_id` y `producto_origen_id`); lo cobrado menos lo reembolsado es igual al nuevo total. Si se retiran todas las líneas, se reembolsa el 100 %. Pruebas: `tests/test_cl11_sobrecobro.py`.

## 9. Cuentas, inicio de sesión y permisos

| Ruta | Quién | Qué hace |
|---|---|---|
| `POST /v1/auth/registro` | Cualquiera | Crea una cuenta de cliente (usuario en minúsculas, contraseña de 8 a 128 caracteres) |
| `POST /v1/auth/login` `{usuario, clave, portal}` | Cualquiera | Abre una sesión de 8 h. Responde el perfil y un token CSRF; la sesión va en una cookie HttpOnly. Con `entregar_token: true` devuelve un token Bearer para la API |
| `POST /v1/auth/logout`, `GET /v1/auth/me` | Con sesión | Cierra la sesión o devuelve la cuenta, sus perfiles y su token CSRF |
| `GET/POST /v1/admin/cuentas`, `PATCH /v1/admin/cuentas/{id}`, `GET /v1/admin/auditoria` | Administración | Lista y da de alta cuentas con perfiles por tienda, activa o desactiva accesos (desactivar cierra sus sesiones) y consulta la auditoría |

Reglas (`app/seguridad.py`, una sola frontera):
- Las contraseñas se guardan con scrypt y sal; los tokens de sesión solo como SHA-256.
- Un usuario inexistente y una contraseña incorrecta responden igual (401).
- Cada cuenta entra solo a su portal: clientes a la tienda; personal y administración al interno.
- Toda operación que cambia algo con cookie exige el encabezado `X-CSRF-Token`.
- La identidad (`Cliente-Id` o `Usuario-Id`) la pone el servidor a partir de la sesión; lo que mande el navegador se descarta.
- Administración consulta todo, pero el servidor rechaza con 403 cualquier operación de pedidos, pagos o inventario hecha por ella.
- Los perfiles por tienda salen de `asignacion_rol` (RNF-06), igual que antes.

## 10. Evidencia del desafío C4

| Pide el desafío | Dónde está |
|---|---|
| Flujos de principio a fin | `tests/test_e2e_c4.py` y el recorrido en navegador de la sección «Interfaz para la demostración» |
| Matriz Requisito → Diseño → Código → Prueba → Resultado (18 casos, todos automatizados) | `docs/matriz_trazabilidad.md`, generada con `python -m scripts.evidencia_final` |
| Defectos, corrección y regresión | `docs/defectos_y_regresiones.md` |
| Decisión de IA aceptada, modificada y rechazada | `docs/defensa_ia.md` |
| Anexo de cambios respecto de la Semana 3, ADR y lo no implementado | `docs/anexo_cambios_semana3.md` |
| Calidad del código (ub-mode: CRAP, duplicación y mutación) | `docs/bitacora_ub_mode.md` |
| CL-14 y CL-15: daño, inspección y reincorporación o desecho | `app/modulos/danos.py`, `tests/test_cl14_cl15_danos.py` |

## Estructura

```
app/
  main.py                arranca FastAPI, junta los módulos y lanza los ciclos de vencimiento, eventos y recuperación
  db.py                  pool de conexiones a PostgreSQL (psycopg); lee DATABASE_URL de .env
  errores.py             errores de negocio con el cuerpo {codigo, mensaje, detalle}
  idempotencia.py        manejo de Clave-Operacion, huella SHA-256 y tabla registro_operacion
  modulos/
    inventario.py        M01 Inventario: CL-01 disponibilidad
    reservas.py          M02 Reservas: CL-02, CL-03, expiración y comprobación de vigencia
    pedidos.py           M03 Pedidos: CL-07 (casos A y B) y consumidor de eventos EV-01
    pagos.py             M04 Pagos y reembolsos: CL-04, CL-05, CL-06, CL-16, CL-17 y recuperación de cobros y reembolsos
    pasarela_simulada.py simulador controlable de la pasarela
    cancelaciones.py     M03 Cancelaciones: CL-13 del cliente y cancelación por faltante tras CL-12
    faltantes.py         M01 y M05: CL-09 conteo, CL-10 faltante, CL-11 retiro de reservas y CL-12 resolución (D-05)
    danos.py             M01: CL-14 daño, CL-15 inspección y reincorporación o desecho
    avisos.py            M06 Avisos: CL-18 con clave estable por origen
    cuentas.py           registro, inicio de sesión, cuentas, perfiles y auditoría
    portal.py            consultas de la página (catálogo, pedidos, operación y pendientes por tienda)
  seguridad.py           frontera de identidad: sesiones, CSRF, contraseñas y permisos de administración
static/
  index.html             portal de clientes (tienda, bolsa, pedidos)
  interno/index.html     portal interno (operación por perfil, cuentas y auditoría)
  js/comun.js            sesión, llamadas a la API y recuperación de operaciones sin respuesta
  js/app.js              vistas y formularios de ambos portales
tests/
  escenarios.py          ayudantes: tiendas, productos y clientes nuevos, reservas vencidas, reconstrucción
  test_inicio.py         pruebas de arranque y de CL-01
  test_checkout.py       CL-02: disponibilidad, reserva, 300 s y carrera
  test_reservas_cierre.py CL-03, expiración, vigencia, carrera liberar/expirar y reconstrucción
  test_c2_pagos.py       CL-04, CL-05, idempotencia y carreras de cobro
  test_c2_confirmacion.py CL-07 casos A y B, pago tardío, carrera con vencimiento y cobro duplicado
  test_c2_reembolsos.py  CL-16, CL-06 con evidencia, roles RESPONSABLE y reintentos CL-17
  test_c2_recuperacion.py recuperación tras caída de intentos INICIADO y eventos EV-01
  escenarios_c3.py       ayudantes de C3: tienda con varios productos, pedido pagado, resolución de faltante
  test_cancelaciones.py  CL-13 del cliente y por faltante, reembolso total y parcial, PREPARACION_INICIADA
  test_avisos.py         CL-18: un aviso por origen
  test_e2e_c4.py         flujo completo: checkout, pago, faltante, cancelación, reembolso e historial
  test_robustez.py       caídas, clave reutilizada, eventos que fallan, montos inválidos y recuperación
  escenarios_faltantes.py ayudantes de C3: usuarios con perfil, conteo, reporte y resolución por la API
  test_cl09_conteo.py    CL-09: 10 contra 8, faltante, sobrante, repetición, perfiles
  test_cl10_faltante.py  CL-10 y CL-11: bloqueo, NO_SURTIBLE, retiro de reservas, las demás líneas continúan
  test_cl11_sobrecobro.py CL-11 + CL-07: reembolso de productos retirados durante el pago
  test_preparar_demo.py  la demo aplica precios y archivos nuevos de db/ sin borrar pedidos
  test_cl12_resolucion.py CL-12: ajuste, D-05 por fecha de compromiso, cancelación parcial y reembolso
  test_at02_limite_300s.py AT-02: vigente a los 299 s y vencida a los 300 s
  test_cl14_cl15_danos.py CL-14 y CL-15: daño, déficit, inspección y dos personas
  test_cuentas.py        sesiones, CSRF, suplantación, portales, administración y auditoría
  test_reembolsos_en_cola.py reembolsos en cola, notificaciones incongruentes y reintento por interbloqueo
  test_ciclos.py         ciclos de fondo: un fallo no tumba el servidor
  conftest.py            activa las cabeceras de identidad solo para las pruebas
db/
  01_modelo_fisico_expressya.sql DDL del SDD v1.1 (no se edita)
  02_precio_producto.sql         agrega el precio unitario al producto
  03_clave_intento_pago.sql      guarda la Clave-Operacion en el intento antes de cobrar (PROPUESTA)
  04_cuentas_y_sesiones.sql      cuentas, sesiones y auditoría de accesos (PROPUESTA)
  90_datos_prueba.sql            datos semilla
  91_catalogo_c1.sql             productos de ropa y sus precios (se pueden editar; la demo los sincroniza)
  91_datos_c1.sql                5 tiendas e inventario inicial
  92_datos_c2.sql                usuario operativo de prueba con rol RESPONSABLE
  93_datos_c3.sql                supervisor y personal de tienda de prueba en las 5 tiendas
docs/
  matriz_trazabilidad.md         matriz de 18 casos con el resultado de la última corrida
  defectos_y_regresiones.md      defectos, causa, corrección y prueba de regresión
  defensa_ia.md                  decisiones de IA aceptada, modificada y rechazada
  anexo_cambios_semana3.md       cambios respecto del diseño, ADR de construcción y lo no implementado
  bitacora_ub_mode.md            bitácora de calidad (CRAP, duplicación, mutación)
evidencia/
  junit_final.xml                resultado de la última corrida de evidencia_final
  c1_resultados.txt              log reproducible de las pruebas de C1
  c2_resultados.txt              log reproducible de las pruebas de C2
.github/
  workflows/pruebas.yml          corre las pruebas en cada pull request
  pull_request_template.md       lo que pide cada pull request
  ISSUE_TEMPLATE/                formatos de tarea y de defecto
scripts/
  evidencia_c1.sh                genera evidencia/c1_resultados.txt
  reconstruir_base.py            reconstruye el esquema expressya cargando todos los SQL en orden
  preparar_demo.py               prepara la base de la demo sin borrar pedidos y aplica lo nuevo de db/
  crear_cuentas_demo.py          crea las cuentas de la demo y guarda sus claves en cuentas_demo.txt
  evidencia_final.py             corre la suite y genera docs/matriz_trazabilidad.md
  evidencia_c2.py                corre todas las pruebas y 20 repeticiones de carrera; guarda evidencia/c2_resultados.txt
  preparar_github.sh             crea etiquetas, hitos e issues de C1 y C2 (solo el PM, una vez)
CONTRIBUTING.md                  reglas del equipo para trabajar en GitHub
```

Si tu base está en otra dirección, copia `.env.example` a `.env` y cambia `DATABASE_URL` ahí (el archivo `.env` no se sube a GitHub). Sin `.env`, la aplicación usa la base del paso 1.

## Trabajo en equipo

Nadie sube cambios directo a `main`: cada tarea va en su rama y entra por pull request con una aprobación y las pruebas en verde. Las reglas completas están en [CONTRIBUTING.md](CONTRIBUTING.md).
