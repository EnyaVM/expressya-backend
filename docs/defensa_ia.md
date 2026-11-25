# Uso crítico de IA: una decisión aceptada, una modificada y una rechazada

Responsable del proyecto: Ian Maximiliano Molina Zavala (PM, Equipo 03).
Herramientas usadas en la construcción: Claude (Anthropic), ChatGPT y Codex. El código y las decisiones son responsabilidad del equipo.

Cada decisión indica qué propuso la IA, qué decidió una persona, por qué, y dónde se comprueba en el repositorio.

## 1. Aceptada: guardar la Clave-Operacion en el intento antes de cobrar

- **Propuesta de la IA:** después de una revisión externa (ChatGPT) que mostró que una caída entre el cobro y el registro perdía la clave, Claude propuso guardar la clave y su huella en `intento_pago` antes de llamar a la pasarela, y bloquear la clave con un candado de sesión en una conexión dedicada.
- **Intervención humana:** Ian pidió revisar qué problemas nuevos traía la solución y que no rompiera acuerdos de semanas anteriores antes de aceptarla. Se aceptó porque cumple la regla del SDD («repetir la misma clave no produce otro efecto») sin cambiar los contratos.
- **Evidencia:** `db/03_clave_intento_pago.sql`; `pagos._crear_intento`; prueba `test_robustez.py::test_caida_tras_el_cobro_conserva_la_clave_y_no_cobra_otra_vez`; defecto DEF-03.

## 2. Modificada: el frontend del portal

- **Propuesta de la IA:** Codex planteó dos frontends nuevos, uno para clientes y otro para el personal.
- **Intervención humana:** Ian la modificó: la misma aplicación y el mismo diseño, con vistas divididas por permisos (comprador: tienda y sus pedidos; administración: panel interno completo), sin que nadie opere como otra persona y respetando las restricciones del SDD.
- **Resultado:** `static/index.html` (clientes) y `static/interno/index.html` (personal) comparten estilos y código (`static/js/comun.js`, `static/js/app.js`). La administración consulta y gestiona cuentas, pero el servidor le impide operar (`app/seguridad.py`, regla `rechazo`).
- **Evidencia:** `test_cuentas.py::test_administracion_consulta_todo_pero_no_opera`; recorrido en navegador descrito en el README.

## 3. Rechazada: «declarar el límite» del sobrecobro en vez de corregirlo

- **Propuesta de la IA:** al terminar C3, Claude dejó documentado como límite que, si un producto se retiraba de un pedido que se estaba pagando, la diferencia cobrada no se reembolsaba sola.
- **Intervención humana:** Ian lo rechazó: un límite conocido que afecta dinero del cliente se corrige, no solo se declara.
- **Resultado:** al confirmar el cobro (CL-07) se crea un reembolso `PRODUCTO_RETIRADO` por cada línea retirada después de iniciar el pago, usando el índice `uq_reembolso_linea_quitada` que ya estaba en el diseño físico. Se quitó el límite del README.
- **Evidencia:** `pedidos.reembolsar_productos_retirados`; `tests/test_cl11_sobrecobro.py`; defecto DEF-10.

## Antecedente del diseño (Semana 3)

El reporte al cliente ya registraba D-08 aceptada, D-05 modificada y D-02 rechazada. En construcción se sumó D-28: la IA recomendó retirar la propuesta PR-01 (una reserva activa por cliente) y Ian decidió conservarla como propuesta retirable, pendiente de Q-18. Sigue así en el código (`uq_reserva_activa_cliente`, error `CHECKOUT_ACTIVO`).
