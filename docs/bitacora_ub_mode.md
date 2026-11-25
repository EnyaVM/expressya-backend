# Bitácora ub-mode — cierre del proyecto (24 y 25 nov 2026)

Responsable: Ian Maximiliano Molina Zavala. Herramientas: crapper (CRAP < 10), dryer (similitud < 0.8) y mutator (mutación), sobre todo `app/`.

## Acciones y razones

| # | Acción | Razón |
|---|---|---|
| 1 | Diagnóstico de precios: `preparar_demo.py` solo cargaba `db/` en una base nueva | Reproducir antes de corregir |
| 2 | Prueba que falla (`test_preparar_demo.py`), catálogo como upsert y registro `archivo_cargado` | TDD; un archivo nuevo se aplica una sola vez |
| 3 | Prueba que falla para el sobrecobro (`test_cl11_sobrecobro.py`), reembolso por línea retirada en CL-07 | Corregir la causa en vez de declarar el límite |
| 4 | `db/04` y `app/seguridad.py`: identidad en una sola frontera; 16 pruebas de sesión, CSRF, suplantación y administración | One decision lives in one place; check data where it enters |
| 5 | CL-14 y CL-15 con 11 pruebas | Faltaban del diseño final |
| 6 | Línea base crapper sobre todo `app/` con cobertura de ramas: 13 funciones ≥ 10 (peor 65.1) | Saber dónde estamos |
| 7 | Refactor de CL-05, pasarela simulada, ciclos de fondo, CL-04, reembolsos, CL-08, CL-07, CL-02/03, cancelaciones y portal; la suite fija el comportamiento antes y después de cada uno | Pin, change structure, prove |
| 8 | Una regresión casi entra: el 401 de un cliente inexistente gastaba la clave. Se conservó y se fijó con prueba | Fix root causes; ninguna prueba lo cubría |
| 9 | dryer: tres duplicados (1.00, 0.82, 1.00) eliminados con funciones y tablas compartidas | Duplicación ≥ 0.8 |
| 10 | Mutación, primera corrida de todo `app/`: 217 muertos y 63 sobrevivientes | Medir la fuerza de las pruebas |
| 11 | Pruebas nuevas para sobrevivientes (`test_mutantes.py`, cuentas, inicio, pagos) | Kill the survivors |
| 12 | Código muerto eliminado: banderas de eventos y envíos (los efectos ya son idempotentes), aviso duplicado en el ciclo de eventos, guardas que el DDL ya garantiza (`s + k <= q`, CHECK de reembolso actual), `solo_si_vencida` redundante, `ensure_ascii` sin efecto | Delete dead code; subtract before you add |
| 13 | Segunda corrida de mutación sobre los archivos cambiados | Comprobar el resultado |
| 14 | `scripts/evidencia_final.py` genera la matriz con commit y versiones | Build the lever |

## Resultado de las compuertas

| Herramienta | Antes | Después |
|---|---|---|
| crapper (CRAP < 10) | 13 funciones ≥ 10 (máx. 65.1) | 0 funciones ≥ 10 (máx. 8.5) |
| dryer (< 0.8) | 3 duplicados | 0 |
| mutator | 217 muertos / 63 sobrevivientes (77.5 %) | 239 muertos / 2 sobrevivientes equivalentes (99.2 %) en los módulos cambiados; `main.repetir` es el tercer equivalente |
| Pruebas | 171 | 247 |

## Mutantes que se aceptan como equivalentes

| Sitio | Por qué no se puede matar |
|---|---|
| `main.repetir`: `while True` → `False` | Bucle infinito de fondo; se prueba una iteración (`ejecutar_ciclo`) |
| `pagos._ejecutar_con_reintento_concurrencia`: `0.02 * intentos` → `/` | Solo cambia el tiempo de espera entre reintentos |
| `pagos._cerrojo_de_clave`: `autocommit=True` → `False` | El candado de sesión de PostgreSQL funciona igual con o sin autocommit |

## Segunda corrida por módulo

| Módulo | Muertos | Sobrevivientes |
|---|---|---|
| `seguridad.py` | 19 | 0 |
| `cuentas.py` | 20 | 0 |
| `danos.py` | 16 | 0 |
| `portal.py` | 7 | 0 |
| `pasarela_simulada.py` | 5 | 0 |
| `cancelaciones.py` | 31 | 0 |
| `pedidos.py` | 33 | 0 |
| `reservas.py` | 36 | 0 |
| `idempotencia.py` | 9 | 0 |
| `pagos.py` | 63 | 2 (equivalentes) |
