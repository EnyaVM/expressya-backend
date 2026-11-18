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

Al arrancar, la aplicación inicia un proceso que cada 5 segundos expira las reservas cuyo plazo de 300 s ya se cumplió.

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

## Estructura

```
app/
  main.py              arranca FastAPI, junta los módulos y lanza la revisión de reservas vencidas
  db.py                pool de conexiones a PostgreSQL (psycopg); lee DATABASE_URL de .env
  errores.py           errores de negocio con el cuerpo {codigo, mensaje, detalle}
  modulos/
    inventario.py      M01 Inventario: CL-01 disponibilidad
    reservas.py        M02 Reservas: CL-02, CL-03, expiración y comprobación de vigencia
    pedidos.py         M03 Pedidos: CL-07
    pagos.py           M04 Pagos y reembolsos: CL-04, CL-05, CL-06, CL-16
    pasarela_simulada.py  simulador controlable de la pasarela
static/
  index.html           la página del cliente
tests/
  escenarios.py        ayudantes: tiendas, productos y clientes nuevos, reservas vencidas, reconstrucción
  test_inicio.py       pruebas de arranque y de CL-01
  test_checkout.py     CL-02: disponibilidad, reserva, 300 s y carrera
  test_reservas_cierre.py   CL-03, expiración, vigencia, carrera liberar/expirar y reconstrucción
db/
  01_modelo_fisico_expressya.sql   DDL del SDD v1.1 (no se edita)
  02_precio_producto.sql           agrega el precio unitario al producto
  90_datos_prueba.sql              datos semilla
  91_datos_c1.sql                  5 tiendas, productos de ropa e inventario inicial
evidencia/
  c1_resultados.txt                log reproducible de las pruebas de C1
.github/
  workflows/pruebas.yml            corre las pruebas en cada pull request
  pull_request_template.md         lo que pide cada pull request
  ISSUE_TEMPLATE/                  formatos de tarea y de defecto
scripts/
  evidencia_c1.sh                  genera evidencia/c1_resultados.txt
  preparar_github.sh               crea etiquetas, hitos e issues de C1 y C2 (solo el PM, una vez)
CONTRIBUTING.md                    reglas del equipo para trabajar en GitHub
```

Si tu base está en otra dirección, copia `.env.example` a `.env` y cambia `DATABASE_URL` ahí (el archivo `.env` no se sube a GitHub). Sin `.env`, la aplicación usa la base del paso 1.

## Trabajo en equipo

Nadie sube cambios directo a `main`: cada tarea va en su rama y entra por pull request con una aprobación y las pruebas en verde. Las reglas completas están en [CONTRIBUTING.md](CONTRIBUTING.md).