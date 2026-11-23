
# Demo C4 - ExpressYa

## 1. Objetivo de la demostración

Mostrar cómo ExpressYa responde cuando un pedido tiene productos faltantes, realizando la cancelación de las cantidades necesarias, el reembolso correspondiente y el aviso al cliente.

## 2. Escenario

Un cliente realiza un pedido de 5 productos con un precio de $100 cada uno.

El sistema cobra $500, pero cuando el personal comienza a preparar el pedido descubre que solamente existen 3 productos disponibles físicamente.

Por lo tanto, es necesario cancelar 2 productos y devolver al cliente $200.

## 3. Desarrollo de la demostración

### Paso 1. Realizar un pedido

- Ingresar como cliente.
- Seleccionar 5 productos.
- Agregar los productos al carrito.
- Confirmar el pedido.

**Resultado esperado:** el sistema registra el pedido correctamente.

### Paso 2. Procesar el pago

- Realizar el pago de $500.
- Consultar el estado del pago.

**Resultado esperado:** el pago queda registrado como exitoso.

### Paso 3. Iniciar la preparación

- El personal comienza a preparar el pedido.
- El sistema cambia el estado a PREPARACION_INICIADA.

**Resultado esperado:** el pedido queda marcado como iniciado.

### Paso 4. Detectar productos faltantes

- El personal revisa el inventario físico.
- Detecta que faltan 2 productos.
- Registra la discrepancia.

**Resultado esperado:** el sistema registra el faltante y actualiza las existencias.

### Paso 5. Cancelar los productos faltantes

- Se solicita cancelar las 2 unidades.
- El sistema valida la cantidad.
- Se registra la cancelación.

**Resultado esperado:** se cancelan únicamente las 2 unidades faltantes.

### Paso 6. Procesar el reembolso

- El sistema calcula el importe de los productos cancelados.
- Se solicita un reembolso de $200.
- Se valida que no supere el importe cobrado.

**Resultado esperado:** se registra el reembolso sin duplicarlo.

### Paso 7. Avisar al cliente

- El sistema genera un aviso de cancelación.
- Se genera la información del reembolso.

**Resultado esperado:** el cliente recibe información sobre los productos cancelados y el dinero devuelto.

### Paso 8. Consultar el resultado final

Se consulta el pedido y se comprueba lo siguiente:

| Concepto | Resultado |
|---|---:|
| Productos solicitados | 5 |
| Productos disponibles | 3 |
| Productos cancelados | 2 |
| Pago original | $500 |
| Reembolso | $200 |
| Total final | $300 |

## 4. Prueba adicional de concurrencia

Se simula que dos clientes intentan reservar la última unidad de un producto al mismo tiempo.

**Resultado esperado:** solamente un cliente consigue reservar la unidad y el otro recibe un mensaje indicando que ya no hay existencias disponibles.

## 5. Prueba adicional de cancelación

Se intenta cancelar un producto después de que inició la preparación.

**Resultado esperado:** el sistema devuelve el código HTTP 409 con el mensaje PREPARACION_INICIADA.

## 6. Decisión técnica rechazada

### Alternativa rechazada: arquitectura de microservicios con bases de datos independientes.

Se decidió no utilizar esta alternativa porque el proyecto necesita mantener sincronizados los pedidos, inventarios, cancelaciones y reembolsos.

Tener varias bases de datos independientes aumentaría la complejidad para mantener la información actualizada.

### Alternativa seleccionada: monolito modular con PostgreSQL.

Se eligió esta arquitectura porque permite organizar el sistema por módulos y utilizar transacciones para mantener la consistencia de la información.

## 7. Conclusión de la demostración

Con esta demostración se busca comprobar que ExpressYa puede responder ante productos faltantes sin afectar incorrectamente el pedido o el dinero del cliente.

También se demuestra la importancia de registrar las discrepancias, controlar las operaciones simultáneas y enviar avisos al cliente.
