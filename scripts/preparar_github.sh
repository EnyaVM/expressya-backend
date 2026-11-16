#!/usr/bin/env bash
set -euo pipefail

IAN=""
P2=""
P3=""
P4=""
P5=""
FECHA_C1="2026-11-15"
FECHA_C2="2026-11-21"

etiqueta() {
  gh label create "$1" --color "$2" --description "$3" --force
}

hito() {
  gh api "repos/{owner}/{repo}/milestones" -f title="$1" -f due_on="$2T23:59:59Z" -f description="$3" --silent
}

tarea() {
  local hito="$1" etiquetas="$2" responsable="$3" titulo="$4" cuerpo="$5"
  local args=(--title "$titulo" --body "$cuerpo" --label "$hito,$etiquetas" --milestone "$hito")
  if [ -n "$responsable" ]; then
    args+=(--assignee "$responsable")
  fi
  gh issue create "${args[@]}"
}

etiqueta C1 1d76db "Desafío C1: núcleo y reserva segura"
etiqueta C2 5319e7 "Desafío C2: pedido y pasarela simulada"
etiqueta contrato 0e8a16 "Implementa un contrato CL-XX"
etiqueta prueba fbca04 "Prueba automática de un escenario AT-XX"
etiqueta pantalla c5def5 "Página que ve el usuario"
etiqueta base-comun bfdadc "Pieza que usan todos los módulos"
etiqueta defecto d73a4a "Algo no hace lo que dice el SDD"
etiqueta docs 0075ca "README, contratos y evidencias"

hito C1 "$FECHA_C1" "Inventario y reservas ejecutables, prueba de concurrencia"
hito C2 "$FECHA_C2" "Pedido y pago simulado, timeout y duplicados"

tarea C1 base-comun "$P4" "Base común: semilla con 5 tiendas y productos" \
"Ampliar \`db/90_datos_prueba.sql\` a 5 Dark Stores con productos de prueba, incluido uno con una sola unidad para AT-01.

Hecho cuando: CL-01 responde para las 5 tiendas."

tarea C1 base-comun "$P4" "Base común: transacciones y Clave-Operacion" \
"Una forma común de abrir transacciones y de guardar \`Clave-Operacion\` en \`registro_operacion\`, para que una solicitud repetida devuelva el mismo resultado.

Hecho cuando: una prueba manda dos veces la misma clave y hay un solo efecto."

tarea C1 contrato "$IAN" "CL-02: crear checkout (reserva de 300 s)" \
"Reserva las unidades por 300 s medidos con \`clock_timestamp()\`, sin dejar el saldo D negativo.

Hecho cuando: responde como dice el anexo OpenAPI y entra a main con su prueba."

tarea C1 prueba "$IAN" "AT-01: última unidad con dos solicitudes a la vez" \
"Prueba automática que lanza dos solicitudes reales al mismo tiempo por la última unidad.

Hecho cuando: exactamente una gana, la otra recibe el error del contrato y D nunca queda negativo."

tarea C1 contrato "$P3" "CL-03: cancelar checkout" \
"Libera la reserva una sola vez si aún no hubo compromiso.

Hecho cuando: cancelar dos veces no libera dos veces."

tarea C1 "contrato,prueba" "$P3" "Expiración de reservas y AT-02" \
"Proceso que vence las reservas ACTIVA al cumplir 300 s, más la comprobación al usar una reserva.

Hecho cuando: la prueba muestra la reserva válida a los 299 s y vencida a los 300 s."

tarea C1 "base-comun,prueba" "$P4" "Historial y reconstrucción del saldo" \
"Registrar cada movimiento y reconstruir el saldo desde los movimientos auditables (\`v_reconstruccion_saldos\`).

Hecho cuando: una prueba compara el saldo guardado con el reconstruido después de reservar, cancelar y vencer."

tarea C1 contrato "$P2" "Pasarela simulada controlable" \
"Simulador que se pueda poner en modo aprobar, rechazar, no responder, responder tarde o mandar el callback dos veces.

Hecho cuando: cada modo se puede elegir desde una prueba."

tarea C1 pantalla "$P5" "Pantallas C1: disponibilidad, checkout y cancelar" \
"Disponibilidad por tienda (CL-01), checkout con cuenta regresiva de 5 minutos y botón de cancelar.

Hecho cuando: se puede reservar y cancelar desde la página."

tarea C1 docs "$P5" "README y evidencias de C1" \
"Instrucciones de instalación y ejecución, y el log de las pruebas de carrera y de expiración.

Hecho cuando: alguien que no programó C1 lo levanta siguiendo solo el README."

tarea C2 contrato "$P2" "CL-04: iniciar intento de pago" \
"Llama al simulador; si no responde en 30 s, el intento queda EN_VERIFICACION (202).

Hecho cuando: responde 201 o 202 como dice el anexo OpenAPI."

tarea C2 "contrato,prueba" "$P2" "CL-05: callback de la pasarela y AT-05" \
"Registra la respuesta de la pasarela sin duplicarla (\`notificacion_pasarela\`).

Hecho cuando: el mismo callback dos veces no duplica cargo, compromiso ni reembolso."

tarea C2 contrato "$P2" "CL-16: reembolso simulado" \
"Reembolso simulado del intento de pago, aplicado una sola vez.

Hecho cuando: pedir el mismo reembolso dos veces deja un solo efecto."

tarea C2 prueba "$P2" "AT-03: pago rechazado" \
"Pago rechazado sin pedido pagado ni inventario retenido.

Hecho cuando: la prueba muestra el pedido sin pagar y que la reserva no queda retenida después de su vencimiento."

tarea C2 "contrato,prueba" "$IAN" "CL-07: confirmar compra (casos A y B) y AT-04" \
"Al recibir EV-01 PagoAprobado: antes del vencimiento confirma el pedido (caso A); después del vencimiento no revive la reserva, deja el pedido SIN_COMPRA y pide el reembolso del 100 % con CL-16 (caso B).

Hecho cuando: AT-04 pasa."

tarea C2 "contrato,prueba" "$P3" "CL-06: resolver a mano pagos EN_VERIFICACION y AT-06" \
"El responsable registra si hubo cobro o no; si queda aprobado se registra EV-01.

Hecho cuando: AT-06 muestra el estado desconocido hasta la conciliación, sin reintento ciego."

tarea C2 "base-comun,prueba" "$P4" "AT-10: recuperación tras una caída entre pasos" \
"Provocar una caída después de guardar el cambio y antes de notificar; al reiniciar, \`evento_interno\` permite terminar.

Hecho cuando: AT-10 pasa y queda la traza de la recuperación."

tarea C2 "base-comun,prueba" "$P4" "Repetir una solicitud por timeout sin doble efecto" \
"Repetir la misma solicitud de pago con la misma \`Clave-Operacion\` devuelve el resultado guardado.

Hecho cuando: hay una prueba con dos envíos y un solo efecto."

tarea C2 pantalla "$P5" "Pantallas C2: pago y estado del pedido" \
"Pantalla de pago y estado del pedido, incluido «en verificación».

Hecho cuando: se ve cada estado del pago desde la página."

tarea C2 docs "$P5" "Contratos documentados de C1 y C2" \
"Comparar \`/docs\` de FastAPI con el anexo OpenAPI y anotar las diferencias.

Hecho cuando: cada ruta de C1 y C2 coincide con el anexo o la diferencia está anotada."
