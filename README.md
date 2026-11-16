# ExpressYa · backend (Equipo 03)

Primer paso de la construcción: la página pregunta si un producto está disponible en una Dark Store (contrato CL-01) y el backend lo lee de PostgreSQL. Usa el mismo stack del SDD v1.1: Python 3.12, FastAPI, psycopg 3, PostgreSQL 16 y pytest.

## Qué necesitas instalar (una sola vez)

- Python 3.12: https://www.python.org/downloads/
- Docker Desktop (para tener PostgreSQL 16 sin instalarlo a mano): https://www.docker.com/products/docker-desktop/
- Visual Studio Code (opcional, para editar).

## 1. Levantar la base de datos

En la Terminal, dentro de esta carpeta:

```bash
docker run --name expressya-db -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=expressya_db -p 5432:5432 -d postgres:16
```

Espera unos segundos y carga el modelo físico del SDD y los datos de prueba:

```bash
docker exec -i expressya-db psql -U postgres -d expressya_db --set=ON_ERROR_STOP=1 < db/01_modelo_fisico_expressya.sql
docker exec -i expressya-db psql -U postgres -d expressya_db --set=ON_ERROR_STOP=1 < db/90_datos_prueba.sql
```

Para apagarla: `docker stop expressya-db`. Para volver a prenderla: `docker start expressya-db`.

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

En la página, escribe la tienda `11111111-1111-1111-1111-111111111111` y el producto `22222222-2222-2222-2222-222222222222` (Leche, 3 unidades: disponible) o `33333333-3333-3333-3333-333333333333` (Pan, 0 unidades: agotado).

## 4. Correr las pruebas

Con la base prendida:

```bash
python -m pytest -q
```

## Estructura

```
app/
  main.py              arranca FastAPI y junta los módulos
  db.py                pool de conexiones a PostgreSQL (psycopg); lee DATABASE_URL de .env
  modulos/
    inventario.py      M01 Inventario: CL-01 disponibilidad
    reservas.py        M02 Reservas: CL-02, CL-03 y vencimiento
    pedidos.py         M03 Pedidos: CL-07
    pagos.py           M04 Pagos y reembolsos: CL-04, CL-05, CL-06, CL-16
    pasarela_simulada.py  simulador controlable de la pasarela
static/
  index.html           la página del cliente
tests/
  test_inicio.py       pruebas con pytest
db/
  01_modelo_fisico_expressya.sql   DDL del SDD v1.1 (no se edita)
  02_..., 03_...                   cambios nuevos a la base, uno por archivo
  90_datos_prueba.sql              datos semilla
.github/
  workflows/pruebas.yml            corre las pruebas en cada pull request
  pull_request_template.md         lo que pide cada pull request
  ISSUE_TEMPLATE/                  formatos de tarea y de defecto
scripts/
  preparar_github.sh               crea etiquetas, hitos e issues de C1 y C2 (solo el PM, una vez)
CONTRIBUTING.md                    reglas del equipo para trabajar en GitHub
```

Si tu base está en otra dirección, copia `.env.example` a `.env` y cambia `DATABASE_URL` ahí (el archivo `.env` no se sube a GitHub). Sin `.env`, la aplicación usa la base del paso 1.

## Trabajo en equipo

Nadie sube cambios directo a `main`: cada tarea va en su rama y entra por pull request con una aprobación y las pruebas en verde. Las reglas completas están en [CONTRIBUTING.md](CONTRIBUTING.md).
