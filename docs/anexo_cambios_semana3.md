# Anexo de cambios respecto del diseño de la Semana 3 (v1.1)

Base: SDD y OpenAPI 1.1 del Equipo 03. Convención: **[DD]** decisión del equipo, **[ST]** supuesto técnico, **PR-xx** propuesta no confirmada por el cliente, **Q-xx** pendiente.

## 1. Cobertura de contratos

| Contrato | Estado en el prototipo | Ruta o módulo |
|---|---|---|
| CL-01 Disponibilidad | Implementado | `GET /v1/darkstores/{id}/productos/{id}/disponibilidad` |
| CL-02 Checkout | Implementado (con PR-01) | `POST /v1/checkouts` |
| CL-03 Cierre de reserva | Implementado | `POST /v1/reservas/{id}/cierre` |
| CL-04 Intento de pago | Implementado | `POST /v1/pedidos/{id}/intentos-pago` |
| CL-05 Notificación de pasarela | Implementado | `POST /v1/pasarela/notificaciones` |
| CL-06 Verificación manual | Implementado | `POST /v1/intentos-pago/{id}/verificacion`, `POST /v1/reembolsos/{id}/verificacion` |
| CL-07 Confirmar compra | Interno | `pedidos.confirmar_compra` |
| CL-08 Separación | Implementado | `POST /v1/lineas-pedido/{id}/separaciones` |
| CL-09 Conteo físico | Implementado | `POST /v1/darkstores/{id}/productos/{id}/conteos` |
| CL-10 Reporte de faltante | Implementado | `POST /v1/lineas-pedido/{id}/faltantes` |
| CL-11 Retiro de reservas | Interno, síncrono | `faltantes.retirar_de_reservas_activas` |
| CL-12 Resolución D-05 | Implementado | `POST /v1/discrepancias/{id}/resolucion` |
| CL-13 Cancelación | Implementado (cliente) e interno (faltante) | `POST /v1/pedidos/{id}/cancelacion`, `cancelaciones.cancelar_cantidad_por_faltante` |
| CL-14 Daño | Implementado | `POST /v1/darkstores/{id}/productos/{id}/danos` |
| CL-15 Inspección y resolución | Implementado | `POST /v1/danos/{id}/inspecciones`, `POST /v1/inspecciones/{id}/resolucion` |
| CL-16 Reembolso | Interno | `pagos.solicitar_reembolso`, `pagos.enviar_reembolso` |
| CL-17 Reintento de reembolso | Implementado | `POST /v1/reembolsos/{id}/reintentos` |
| CL-18 Aviso | Interno | `avisos.registrar_aviso` |

## 2. Decisiones de construcción (ADR)

| ADR | Decisión | Motivo | Efecto en el diseño |
|---|---|---|---|
| ADR-C1 [DD] | Python 3.12 + FastAPI + psycopg 3, SQL explícito (ADR-09 revisado) | Lenguaje que domina el equipo | Ninguno en DDL ni contratos |
| ADR-C2 [DD] | La Clave-Operacion de CL-04 se guarda en `intento_pago` antes de cobrar, con candado de sesión en conexión dedicada | Una caída a medio camino no debe permitir un segundo cobro (DEF-03) y el pool no debe agotarse (DEF-02) | Migración `db/03`; mismo contrato |
| ADR-C3 [DD] | CL-11 corre en la misma transacción que detecta el faltante; el EV-03 se registra ya procesado | Atomicidad sin un consumidor más | `x-operaciones-internas` CL-11 sin cambio de efecto |
| ADR-C4 PR | Un conteo menor que E pasa la diferencia de E a N «en revisión»; si no cubre R + C se bloquea el producto (AT-4) | Respuestas del cliente P4 y P8; AT-07 | Usa saldos y movimientos existentes |
| ADR-C5 [DD] | CL-12 usa una sola regla: `unidades_validadas` son las unidades físicas confirmadas | Evita dos significados de la misma cifra | Esquema `ResolucionSolicitud` sin cambio |
| ADR-C6 [DD] | Reembolso `PRODUCTO_RETIRADO` por línea retirada durante el pago, al confirmar | Lo cobrado no debe superar el total vigente (DEF-10) | Usa `reserva_origen_id`, `producto_origen_id` y `uq_reembolso_linea_quitada` del DDL |
| ADR-C7 [DD] | Sesiones opacas guardadas en el servidor (una cookie HttpOnly + CSRF por pestaña y portal, o Bearer para la API) en vez de JWT | Cerrar sesión o desactivar una cuenta corta el acceso al instante (un JWT sigue válido hasta que caduca); el token nunca se guarda en claro | `securitySchemes` describe JWT; la identidad sigue saliendo de `asignacion_rol` (RNF-06) |
| ADR-C8 [DD] | Cuentas de cliente, de personal y de administración; administración consulta y da de alta, pero no opera | Sin suplantación (DEF-11) | Migración `db/04` (cuenta, sesion, auditoria_cuenta) |
| ADR-C9 [ST] | Pasarela simulada en `/simulador/modo`, solo administración | El desafío permite simular pagos | Fuera de los CL |
| ADR-C10 [DD] | Consultas de interfaz en `/v1/portal/*` | La página necesita listas que el OpenAPI no define | Solo lectura, con permisos por perfil |

## 3. Cambios en la base de datos

| Archivo | Cambio |
|---|---|
| `db/02_precio_producto.sql` | Precio del producto para calcular totales |
| `db/03_clave_intento_pago.sql` | Clave y huella en el intento (ADR-C2) |
| `db/04_cuentas_y_sesiones.sql` | Cuentas, sesiones y auditoría (ADR-C7, ADR-C8) |
| `db/91_catalogo_c1.sql` | Catálogo de C1 como upsert, para editar precios sin reconstruir la demo |

## 4. Lo que no se implementa, con razón, riesgo y efecto

| Elemento | Razón | Riesgo | Efecto en escenarios |
|---|---|---|---|
| Autenticación de la pasarela en CL-05 | Depende de Q-13 (capacidades del proveedor) | Una notificación falsa podría aplicarse | Ninguno en AT-05; se conserva cada notificación y su referencia |
| Entrega real de avisos (correo, SMS, push) | El desafío permite avisos visibles en bitácora | Ninguno para la demo | Los avisos se guardan y se ven en la página |
| Respaldos, RPO/RTO y metas de servicio | Dependen de Q-11 y Q-12 | No hay compromiso de recuperación ante desastre | Ninguno en AT-01 a AT-10 |
| Momento exacto de «inicio de preparación» | Q-07; se usa la primera unidad separada (s > 0) | Si el cliente define otro evento, cambia PREPARACION_INICIADA | AT de cancelación |
| Plazo máximo para validar un faltante | Q-16 | Un producto puede quedar bloqueado mientras no se resuelva | Ninguno en AT-07 y AT-08 |
| PR-01 una reserva activa por cliente | Propuesta del equipo pendiente de Q-18 | Si el cliente la rechaza, se retira el índice y el código `CHECKOUT_ACTIVO` | AT-01 no depende de ella |
| Quién es «persona» o «rol» en la doble autorización | Q-06; se exige que inspector y autorizador sean usuarios distintos | Si el cliente pide otra regla, cambia la validación D-09 | CL-15 |

## 5. Preguntas para el profesor y el cliente

Q-18 (PR-01), Q-06 (doble autorización: personas o roles), Q-07 (inicio de preparación), Q-16 (plazo de faltantes), Q-11 (RPO/RTO), Q-12 (metas de servicio) y Q-13 (capacidades de la pasarela). Ninguna se trata como aprobada; el código usa la opción indicada en la sección 4 hasta tener respuesta.
