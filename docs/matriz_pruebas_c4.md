
# Matriz de pruebas C4 - ExpressYa

## 1. Objetivo

Comprobar que el sistema ExpressYa maneje correctamente las cancelaciones de productos, los reembolsos, los avisos al cliente, las discrepancias de inventario y las operaciones simultáneas.

## 2. Casos de prueba

| ID | Caso de prueba | Resultado esperado |
|---|---|---|
| C4-01 | Cancelar productos por falta de inventario | Se cancela únicamente la cantidad faltante. |
| C4-02 | Cancelación solicitada por el cliente antes de iniciar preparación | El sistema permite cancelar los productos. |
| C4-03 | Cliente intenta cancelar después de iniciar preparación | El sistema responde con error 409 PREPARACION_INICIADA. |
| C4-04 | Repetir una solicitud de cancelación | No se genera un segundo reembolso. |
| C4-05 | Intentar reembolsar más dinero del cobrado | El sistema rechaza la operación. |
| C4-06 | Registrar una discrepancia de inventario | Se registra el faltante y se actualizan las existencias. |
| C4-07 | Dos clientes intentan reservar la última unidad | Solamente uno consigue reservarla. |
| C4-08 | Registrar dos veces el mismo faltante | No se duplica el ajuste de inventario. |
| C4-09 | Enviar un aviso de cancelación | El cliente recibe el aviso correspondiente. |
| C4-10 | Repetir el envío del mismo aviso | No se genera un aviso duplicado. |

## 3. Prueba de integración E2E

Se realiza un flujo completo para comprobar que los módulos trabajen correctamente.

### Datos iniciales

- Cliente: 1
- Pedido: 100
- Productos solicitados: 5
- Precio por producto: $100
- Total cobrado: $500
- Productos faltantes: 2

### Procedimiento

1. El cliente realiza el checkout.
2. El sistema registra el pedido.
3. Se procesa el pago por $500.
4. El personal comienza a preparar el pedido.
5. Se detecta que faltan 2 productos.
6. Se registra la discrepancia de inventario.
7. Se cancelan las 2 unidades faltantes.
8. Se procesa un reembolso de $200.
9. Se envía un aviso al cliente.
10. Se consulta el estado final del pedido.

### Resultado esperado

| Concepto | Resultado |
|---|---:|
| Productos solicitados | 5 |
| Productos entregados | 3 |
| Productos cancelados | 2 |
| Total cobrado | $500 |
| Reembolso | $200 |
| Cobro neto | $300 |

## 4. Criterios de aceptación

- No se permite cancelar cuando la preparación ya inició.
- No se permite reembolsar más dinero del cobrado.
- Las cancelaciones repetidas no deben generar reembolsos duplicados.
- Los faltantes deben quedar registrados.
- El inventario debe actualizarse correctamente.
- Los clientes deben recibir avisos sobre las cancelaciones y reembolsos.
- Las operaciones simultáneas no deben provocar existencias negativas.

## 5. Defectos que se deben detectar

| Defecto | Consecuencia |
|---|---|
| Reembolso duplicado | Pérdida económica. |
| Inventario desactualizado | Venta de productos inexistentes. |
| Cancelación después de iniciar preparación | Problemas operativos. |
| Avisos duplicados | Confusión del cliente. |
| Dos reservas de una misma unidad | Sobreventa de productos. |
| Ajuste duplicado de faltantes | Inventario incorrecto. |

## 6. Conclusión

Las pruebas C4 permiten comprobar que ExpressYa gestione correctamente los pedidos cuando existen productos faltantes. También ayudan a evitar errores en inventario, cancelaciones y reembolsos, mejorando la seguridad y confiabilidad del sistema.
