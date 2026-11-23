# ExpressYa · backend (Equipo 03)

Backend de ExpressYa con el stack del SDD v1.1: Python 3.12, FastAPI, psycopg 3, PostgreSQL 16 y pytest. Incluye la consulta de disponibilidad (CL-01) y el núcleo persistente de reservas (C1): checkout con reserva de 300 s (CL-02), liberación (CL-03), expiración, historial de movimientos y reconstrucción de saldos.

## Qué necesitas instalar (una sola vez)

- Python 3.12: https://www.python.org/downloads/
- Docker Desktop (para tener PostgreSQL 16 sin instalarlo a mano): https://www.docker.com/products/docker-desktop/
- Visual Studio Code (opcional, para editar).

## 1. Levantar la base de datos

En la Terminal, dentro de esta carpeta:

```bash
docker run --name expressya-db -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=expressya_db -p 5432:5432 -d postgres:16
```

Espera unos segundos y carga los archivos de `db/` en orden (01, 02, 90, 91):

```bash
docker exec -i expressya-db psql -U postgres -d expressya_db --set=ON_ERROR_STOP=1 < db/01_modelo_fisico_expressya.sql
docker exec -i expressya-db psql -U postgres -d expressya_db --set=ON_ERROR_STOP=1 < db/02_precio_producto.sql
docker exec -i expressya-db psql -U postgres -d expressya_db --set=ON_ERROR_STOP=1 < db/90_datos_prueba.sql
docker exec -i expressya-db psql -U postgres -d expressya_db --set=ON_ERROR_STOP=1 < db/91_datos_c1.sql
docker exec -i expressya-db psql -U postgres -d expressya_db --set=ON_ERROR_STOP=1 < db/92_datos_c2.sql
```

Con eso quedan 5 Dark Stores, 5 productos de ropa con precio y 25 filas de inventario, cada una con su movimiento inicial.

Para apagarla: `docker stop expressya-db`. Para volver a prenderla: `docker start expressya-db`.

Para empezar de cero, borra el esquema y vuelve a cargar los cuatro archivos:

```bash
docker exec expressya-db psql -U postgres -d expressya_db -c "DROP SCHEMA IF EXISTS expressya CASCADE"
```

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

En la página, escribe la tienda `11111111-1111-1111-1111-111111111111` y el producto `22222222-2222-2222-2222-222222222222` (Playera básica negra M, 3 unidades: disponible) o `33333333-3333-3333-3333-333333333333` (Jeans slim azul 32, 0 unidades: agotado).

Al arrancar, la aplicación inicia un proceso que cada 5 segundos expira las reservas cuyo plazo de 300 s ya se cumplió,procesan eventos internos EV-01 pendientes y recuperan intentos de pago huérfanos en estado INICIADO.

## 4. Correr las pruebas

Con la base prendida:

```bash
python -m pytest -q
```

Las pruebas crean sus propias tiendas, productos y clientes, así que se pueden repetir sin limpiar nada (el historial de movimientos es de solo inserción y no se borra).

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

Los 30 s de espera, el tope de 5 intentos por evento y el cerrojo por `Clave-Operacion` en una conexión aparte son decisiones `PROPUESTA` del equipo, no requisitos del cliente.

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
    pagos.py             M04 Pagos y reembolsos: CL-04, CL-05, CL-06, CL-16, CL-17 y recuperación
    pasarela_simulada.py simulador controlable de la pasarela
static/
  index.html             la página del cliente
tests/
  escenarios.py          ayudantes: tiendas, productos y clientes nuevos, reservas vencidas, reconstrucción
  test_inicio.py         pruebas de arranque y de CL-01
  test_checkout.py       CL-02: disponibilidad, reserva, 300 s y carrera
  test_reservas_cierre.py CL-03, expiración, vigencia, carrera liberar/expirar y reconstrucción
  test_c2_pagos.py       CL-04, CL-05, idempotencia y carreras de cobro
  test_c2_confirmacion.py CL-07 casos A y B, pago tardío, carrera con vencimiento y cobro duplicado
  test_c2_reembolsos.py  CL-16, CL-06 con evidencia, roles RESPONSABLE y reintentos CL-17
  test_c2_recuperacion.py recuperación tras caída de intentos INICIADO y eventos EV-01
db/
  01_modelo_fisico_expressya.sql DDL del SDD v1.1 (no se edita)
  02_precio_producto.sql         agrega el precio unitario al producto
  90_datos_prueba.sql            datos semilla
  91_datos_c1.sql                5 tiendas, productos de ropa e inventario inicial
  92_datos_c2.sql                usuario operativo de prueba con rol RESPONSABLE
evidencia/
  c1_resultados.txt              log reproducible de las pruebas de C1
  c2_resultados.txt              log reproducible de las pruebas de C2
.github/
  workflows/pruebas.yml          corre las pruebas en cada pull request
  pull_request_template.md       lo que pide cada pull request
  ISSUE_TEMPLATE/                formatos de tarea y de defecto
scripts/
  evidencia_c1.sh                genera evidencia/c1_resultados.txt
  reconstruir_base.py            reconstruye el esquema expressya cargando todos los SQL en orden
  evidencia_c2.py                corre todas las pruebas y 20 repeticiones de carrera; guarda evidencia/c2_resultados.txt
  preparar_github.sh             crea etiquetas, hitos e issues de C1 y C2 (solo el PM, una vez)
CONTRIBUTING.md                  reglas del equipo para trabajar en GitHub
```

Si tu base está en otra dirección, copia `.env.example` a `.env` y cambia `DATABASE_URL` ahí (el archivo `.env` no se sube a GitHub). Sin `.env`, la aplicación usa la base del paso 1.

## Trabajo en equipo

Nadie sube cambios directo a `main`: cada tarea va en su rama y entra por pull request con una aprobación y las pruebas en verde. Las reglas completas están en [CONTRIBUTING.md](CONTRIBUTING.md).