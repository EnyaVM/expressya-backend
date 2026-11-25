'use strict';

const PORTAL = Sesion.portal;
const ROLES = ['PICKER', 'PERSONAL_TIENDA', 'SUPERVISOR', 'INSPECTOR', 'AUTORIZADOR', 'RESPONSABLE'];
const esAdmin = () => Sesion.cuenta?.tipo === 'ADMINISTRADOR';

const estado = {
  vista: null,
  tiendas: [],
  tienda: sessionStorage.getItem('expressya-tienda'),
  catalogo: [],
  filtro: 'todos',
  bolsa: JSON.parse(sessionStorage.getItem('expressya-bolsa') || '[]'),
  opsTienda: sessionStorage.getItem('expressya-ops-tienda'),
  ops: null,
  opsFiltro: 'TODOS',
  opsBusqueda: '',
  pedidoAbierto: null,
  detalle: null,
  cuentas: [],
  despuesDeEntrar: null
};

$('#hero-art').innerHTML = prenda('Playera básica negra');

function categoria(p) {
  return /jeans/i.test(p.nombre) ? 'inferior' : /calcetas/i.test(p.nombre) ? 'accesorios' : 'superior';
}

function aplicarPermisos() {
  const c = Sesion.cuenta;
  $$('[data-portal-only]').forEach(e => { e.hidden = e.dataset.portalOnly !== PORTAL; });
  $$('[data-requiere]').forEach(e => {
    if (e.dataset.portalOnly && e.dataset.portalOnly !== PORTAL) return;
    e.hidden = e.dataset.requiere === 'admin' ? !esAdmin() : !c;
  });
  $('#cuenta-btn').textContent = c ? c.nombre.split(' ')[0] : 'Iniciar sesión';
  $('#cuenta-btn').hidden = PORTAL === 'interno' && !c;
  actualizarPendiente();
}

function vistaInicial() {
  if (PORTAL === 'cliente') return 'tienda';
  return Sesion.cuenta ? 'operacion' : 'acceso';
}

async function cambiarVista(siguiente) {
  if (siguiente === 'inicio') siguiente = vistaInicial();
  if (PORTAL === 'interno' && !Sesion.cuenta) siguiente = 'acceso';
  if (['cuentas', 'auditoria'].includes(siguiente) && !esAdmin()) siguiente = vistaInicial();
  if (siguiente === 'pedidos' && !Sesion.cuenta) {
    pedirAcceso('Inicia sesión para ver tus pedidos.', () => cambiarVista('pedidos'));
    return;
  }
  estado.vista = siguiente;
  $$('.view').forEach(e => { e.hidden = e.id !== `view-${siguiente}`; });
  $$('.nav').forEach(e => e.classList.toggle('active', e.dataset.view === siguiente));
  window.scrollTo({ top: 0, behavior: 'smooth' });
  if (siguiente === 'pedidos') await cargarPedidos();
  if (siguiente === 'operacion') await cargarOperacion();
  if (siguiente === 'cuentas') await cargarCuentas();
  if (siguiente === 'auditoria') await cargarAuditoria();
}

function limpiarDatosSensibles() {
  ['#orders', '#ops-content', '#ops-roles', '#accounts', '#audit', '#drawer-content'].forEach(s => { if ($(s)) $(s).innerHTML = ''; });
  estado.ops = null;
  estado.cuentas = [];
  estado.pedidoAbierto = null;
  estado.detalle = null;
  $$('dialog[open]').forEach(d => d.close());
}

Sesion.alPerderla = () => {
  limpiarDatosSensibles();
  aplicarPermisos();
  cambiarVista(vistaInicial());
  if (PORTAL === 'cliente') pedirAcceso('Tu sesión terminó. Inicia sesión nuevamente para continuar.');
  toast('Tu sesión terminó o ya no es válida. Inicia sesión nuevamente.', true);
};

function actualizarPendiente() {
  $('#pendiente-banner').hidden = !Pendientes.pendiente;
}
Pendientes.alCambiar = actualizarPendiente;

/* ---------- Acceso ---------- */

function pedirAcceso(motivo, despues = null) {
  if (PORTAL === 'interno') { cambiarVista('acceso'); return; }
  estado.despuesDeEntrar = despues;
  $('#acceso-motivo').textContent = motivo || 'Tu bolsa se conserva mientras inicias sesión.';
  $('#acceso-error').hidden = true;
  mostrarPestana('entrar');
  $$('dialog[open]').forEach(d => { if (d.id !== 'acceso') d.close(); });
  if (!$('#acceso').open) $('#acceso').showModal();
}

function mostrarPestana(nombre) {
  $$('[data-tab]').forEach(b => {
    const activa = b.dataset.tab === nombre;
    b.classList.toggle('selected', activa);
    b.setAttribute('aria-selected', String(activa));
  });
  $('#form-entrar').hidden = nombre !== 'entrar';
  $('#form-registro').hidden = nombre !== 'registro';
  $('#acceso-titulo').textContent = nombre === 'entrar' ? 'Entra para continuar.' : 'Crea tu cuenta.';
}

function errorEnFormulario(destino, e) {
  const campos = e.detalle?.campos?.length ? ` (${e.detalle.campos.map(c => c.split('.').pop()).join(', ')})` : '';
  destino.textContent = e.message + campos;
  destino.hidden = false;
}

async function alEntrar() {
  aplicarPermisos();
  if (PORTAL === 'interno') {
    await prepararTiendasInternas();
    await cambiarVista('operacion');
  }
  if (Pendientes.pendiente) toast('Tienes una operación sin respuesta. Recupérala antes de continuar.', true);
  const despues = estado.despuesDeEntrar;
  estado.despuesDeEntrar = null;
  if (despues) await despues();
}

$('#form-entrar').addEventListener('submit', async ev => {
  ev.preventDefault();
  const f = ev.target, d = Object.fromEntries(new FormData(f));
  try {
    await iniciarSesion(d.usuario.trim(), d.clave);
    f.reset();
    $('#acceso').close();
    toast(`Hola, ${Sesion.cuenta.nombre.split(' ')[0]}.`);
    await alEntrar();
  } catch (e) { errorEnFormulario($('#acceso-error'), e); }
});

$('#form-registro').addEventListener('submit', async ev => {
  ev.preventDefault();
  const f = ev.target, d = Object.fromEntries(new FormData(f));
  if (d.clave !== d.confirmacion) {
    errorEnFormulario($('#acceso-error'), new Error('Las contraseñas no coinciden.'));
    return;
  }
  try {
    await api('/v1/auth/registro', { method: 'POST', body: { usuario: d.usuario.trim(), nombre: d.nombre.trim(), clave: d.clave } });
    await iniciarSesion(d.usuario.trim(), d.clave);
    f.reset();
    $('#acceso').close();
    toast('Cuenta creada. Ya puedes reservar.');
    await alEntrar();
  } catch (e) { errorEnFormulario($('#acceso-error'), e); }
});

$('#form-interno').addEventListener('submit', async ev => {
  ev.preventDefault();
  const f = ev.target, d = Object.fromEntries(new FormData(f)), error = f.querySelector('.form-error');
  error.hidden = true;
  try {
    await iniciarSesion(d.usuario.trim(), d.clave);
    f.reset();
    await alEntrar();
  } catch (e) { errorEnFormulario(error, e); }
});

$('#cuenta-btn').onclick = () => {
  const c = Sesion.cuenta;
  if (!c) { pedirAcceso(); return; }
  $('#mi-cuenta-nombre').textContent = c.nombre;
  $('#mi-cuenta-usuario').textContent = `${c.usuario} · ${label(c.tipo)}`;
  $('#mi-cuenta-perfiles').innerHTML = c.asignaciones.map(a => `<span class="chip">${esc(label(a.rol))} · ${esc(a.tienda)}</span>`).join('');
  $('#mi-cuenta').showModal();
};

$('#salir').onclick = async () => {
  try { await cerrarSesion(); } catch (e) { toast(mensajeError(e), true); }
  if (PORTAL === 'cliente') { estado.bolsa = []; guardarBolsa(); }
  limpiarDatosSensibles();
  aplicarPermisos();
  await cambiarVista(vistaInicial());
  toast('Sesión cerrada.');
};

$('#recuperar').onclick = async () => {
  try {
    const r = await recuperarPendiente();
    if (!r || !r.datos) return;
    if (r.solicitud.path === '/v1/checkouts') {
      estado.bolsa = [];
      guardarBolsa();
      await abrirPedido(r.datos.pedido.pedido_id);
    } else if (estado.pedidoAbierto) {
      await abrirPedido(estado.pedidoAbierto);
    }
    await refrescarVistaActual();
  } catch (e) { toast(mensajeError(e), true); }
};

/* ---------- Tienda (cliente) ---------- */

function guardarBolsa() {
  sessionStorage.setItem('expressya-bolsa', JSON.stringify(estado.bolsa));
  $('#bag-count').textContent = estado.bolsa.reduce((a, x) => a + x.qty, 0);
}

async function cargarCatalogo() {
  const tienda = estado.tienda;
  const productos = await leer(`/v1/portal/catalogo/${tienda}`);
  if (tienda !== estado.tienda) return;
  estado.catalogo = productos;
  pintarCatalogo();
  $('#catalog-status').textContent = `${productos.length} esenciales · precios en MXN`;
}

function pintarCatalogo() {
  $('#products').innerHTML = estado.catalogo.filter(p => estado.filtro === 'todos' || categoria(p) === estado.filtro).map(p => `
    <article class="product">
      <div class="product-art">${prenda(p.nombre)}</div>
      <div class="product-info">
        <span class="availability ${p.disponibilidad === 'agotado' ? 'sold' : ''}">${p.disponibilidad === 'disponible' ? 'Disponible en tu tienda' : 'Por ahora, agotado'}</span>
        <h3>${esc(p.nombre)}</h3>
        <div class="product-bottom"><span class="price">${money(p.precio)}</span>
          <button class="add" data-add="${p.producto_id}" ${p.disponibilidad === 'agotado' ? 'disabled' : ''} aria-label="Agregar ${esc(p.nombre)}">Agregar</button></div>
      </div>
    </article>`).join('') || '<div class="empty">No hay productos en esta categoría.</div>';
}

function pintarBolsa() {
  estado.pedidoAbierto = null;
  estado.detalle = null;
  const total = estado.bolsa.reduce((s, p) => s + Number(p.precio) * p.qty, 0);
  const tienda = estado.tiendas.find(s => s.darkstore_id === estado.tienda)?.nombre || '';
  abrirPanel('TU BOLSA', `<h2>Buena elección.</h2><p class="muted">${esc(tienda)}</p>${estado.bolsa.length ? estado.bolsa.map(p => `
    <div class="cart-line"><div class="mini-art">${prenda(p.nombre)}</div>
      <div class="line-info"><h3>${esc(p.nombre)}</h3><span>${money(p.precio)}</span>
        <div class="stepper"><button data-qty="${p.producto_id}" data-delta="-1" aria-label="Quitar una unidad de ${esc(p.nombre)}">−</button><span>${p.qty}</span><button data-qty="${p.producto_id}" data-delta="1" aria-label="Agregar una unidad de ${esc(p.nombre)}">+</button></div>
      </div><strong>${money(Number(p.precio) * p.qty)}</strong></div>`).join('') + `
    <div class="row total"><span>Total</span><span>${money(total)}</span></div>
    <button class="primary full" data-action="checkout" data-mutate>${Sesion.cuenta ? 'Reservar y continuar' : 'Inicia sesión para reservar'}</button>
    <p class="small">Tu bolsa todavía no reserva inventario. Al continuar, el servidor confirma la disponibilidad y reserva durante 5 minutos.</p>`
    : '<div class="empty"><h3>Tu próxima elección empieza aquí.</h3><p>Agrega un esencial de la colección.</p><button class="primary" data-close="drawer">Explorar colección</button></div>'}`);
}

async function reservar() {
  if (!Sesion.cuenta) {
    pedirAcceso('Inicia sesión o crea tu cuenta para reservar. Tu bolsa se conserva.', async () => pintarBolsa());
    return;
  }
  const datos = await mutacion('/v1/checkouts', { darkstore_id: estado.tienda, lineas: estado.bolsa.map(p => ({ producto_id: p.producto_id, cantidad: p.qty })) });
  if (!datos) return;
  estado.bolsa = [];
  guardarBolsa();
  await abrirPedido(datos.pedido.pedido_id);
  await cargarCatalogo();
  toast('Tus prendas quedaron reservadas. Tienes 5 minutos para pagar.');
}

/* ---------- Pedidos ---------- */

function tarjetaPedido(p) {
  return `<article class="order-card"><div class="row"><div><h3>Pedido ${short(p.pedido_id)}</h3><p>${esc(p.tienda || '')}${p.tienda ? ' · ' : ''}${date(p.fecha_creacion)}</p></div>${badge(p.estado)}</div>
    <div class="row" style="margin-top:22px"><strong>${money(p.total_vigente)}</strong><button class="secondary" data-order="${p.pedido_id}">${PORTAL === 'interno' ? 'Gestionar pedido' : 'Ver pedido'}</button></div></article>`;
}

async function cargarPedidos() {
  $('#orders').innerHTML = '<p class="muted">Consultando tus pedidos…</p>';
  const lista = await leer('/v1/portal/pedidos');
  $('#orders').innerHTML = lista.map(tarjetaPedido).join('') || '<div class="empty"><h3>Tu primera compra te espera.</h3><p>Cuando reserves tus prendas, podrás seguirlas aquí.</p><button data-view="tienda" class="primary">Ir a la colección</button></div>';
}

async function abrirPedido(id, silencioso = false) {
  const p = await leer(`/v1/portal/pedido/${id}`);
  estado.pedidoAbierto = id;
  estado.detalle = p;
  desfaseServidor = new Date(p.hora_servidor).getTime() - Date.now();
  const html = PORTAL === 'interno' ? detalleInterno(p) : detalleCliente(p);
  if (silencioso) refrescarPanel(html);
  else abrirPanel(PORTAL === 'interno' ? 'GESTIÓN DE PEDIDO' : 'TU COMPRA', html);
  tic();
}

function cabeceraPedido(p) {
  const confirmado = p.estado === 'CONFIRMADO', activa = p.estado_reserva === 'ACTIVA', preparando = p.lineas.some(l => l.s > 0), ultimo = p.pagos.at(-1);
  return `<h2>Pedido ${short(p.pedido_id)}</h2><p class="muted">${esc(p.tienda)} · ${date(p.fecha_creacion)}</p>
    <div class="status-hero">${badge(p.estado)}
      <h3>${confirmado ? (preparando ? 'Ya estamos preparándolo.' : 'Listo para preparar.') : p.estado === 'CANCELADO' ? 'Cancelación registrada.' : activa ? 'Un poco más cerca.' : 'Consulta el estado de la compra.'}</h3>
      ${activa ? '<p>Tiempo restante de la reserva</p><div id="countdown" class="countdown"></div>' : `<p>${esc(label(p.estado_reserva))}</p>`}
      ${ultimo ? `<p style="margin:15px 0 0">${badge(ultimo.estado)}</p>` : ''}
      ${p.ajuste_pendiente ? '<p class="small">Hay una devolución en curso para este pedido.</p>' : ''}
    </div>`;
}

function pieDePedido(p) {
  const ultima = Pendientes.ultima;
  return `<div class="actions"><button class="secondary" data-action="refresh-detail">Actualizar estado</button>${ultima && ultima.path.includes(p.pedido_id) ? '<button class="secondary" data-action="replay" data-mutate>Repetir última solicitud</button>' : ''}</div>`;
}

function detalleCliente(p) {
  const activa = p.estado_reserva === 'ACTIVA', abierto = p.pagos.some(i => ['INICIADO', 'EN_VERIFICACION'].includes(i.estado)), confirmado = p.estado === 'CONFIRMADO', preparando = p.lineas.some(l => l.s > 0);
  let html = cabeceraPedido(p) + lineasDePedido(p) + `<div class="row total"><span>Total vigente</span><span>${money(p.total_vigente)}</span></div>`;
  if (activa && p.estado === 'PENDIENTE_DE_PAGO' && !abierto) html += `<button class="primary full" data-action="pay" data-mutate>Pagar ${money(p.total_vigente)}</button><p class="small">Pasarela de prueba: no necesitas una tarjeta real ni se hace ningún cargo.</p>`;
  if (abierto) html += '<div class="notice warn">El pago está en verificación. Espera la confirmación; el responsable de la tienda lo revisa. No inicies otro cobro.</div>';
  if (activa && p.estado === 'PENDIENTE_DE_PAGO') html += '<button class="secondary full" data-action="release" data-mutate>Liberar reserva</button>';
  if (confirmado) html += `<button class="secondary full" data-action="cancel" data-mutate>Cancelar pedido y solicitar devolución</button>${preparando ? '<p class="small">La preparación ya inició. El servidor rechazará la cancelación del cliente.</p>' : ''}`;
  html += pieDePedido(p);
  if (p.pagos.length) html += `<h3 class="sub">Pagos</h3>${p.pagos.map(i => `<div class="detail-line"><div class="row">${badge(i.estado)}<strong>${money(i.monto)}</strong></div><p class="small">${date(i.fecha_inicio)}</p></div>`).join('')}`;
  html += reembolsosDePedido(p);
  if (p.avisos.length) html += `<h3 class="sub">Avisos de tu pedido</h3>${p.avisos.map(a => `<div class="notice">${esc(label(a.tipo))}<br><span class="small">${date(a.fecha)}</span></div>`).join('')}`;
  return html + '<p class="small">El estado mostrado proviene del servidor.</p>';
}

function detalleInterno(p) {
  const roles = rolesEn(p.darkstore_id), puedeSeparar = roles.includes('PICKER'), puedeRevisar = roles.includes('RESPONSABLE');
  const confirmado = p.estado === 'CONFIRMADO', ultimo = p.pagos.at(-1);
  const puedeReportar = roles.some(r => ['PICKER', 'PERSONAL_TIENDA'].includes(r));
  const pendienteDeSurtir = l => confirmado && l.linea_id && l.estado === 'ASIGNADA' && l.cantidad - l.s - l.k > 0;
  let html = cabeceraPedido(p) + lineasDePedido(p, l => (puedeSeparar && pendienteDeSurtir(l)
    ? `<form data-separate="${l.linea_id}" class="row inline-form"><label>Cantidad<input name="cantidad" type="number" value="1" min="1" max="${l.cantidad - l.s - l.k}" required></label><button class="primary" data-mutate>Separar unidades</button></form>` : '')
    + (puedeReportar && pendienteDeSurtir(l)
    ? `<form data-op="faltante:${l.linea_id}" class="row inline-form"><label>Evidencia del faltante<input name="evidencia" required minlength="3" placeholder="Hueco vacío en el anaquel"></label><button class="secondary" data-mutate>Reportar faltante (CL-10)</button></form>` : ''));
  html += `<div class="row total"><span>Total vigente</span><span>${money(p.total_vigente)}</span></div>` + pieDePedido(p);
  if (p.pagos.length) html += `<h3 class="sub">Pagos</h3>${p.pagos.map(i => `<div class="detail-line"><div class="row">${badge(i.estado)}<strong>${money(i.monto)}</strong></div>
    <p class="small">${date(i.fecha_inicio)} · ${esc(i.referencia_externa || 'Sin confirmación externa')}</p>
    ${puedeRevisar && i.estado === 'EN_VERIFICACION' ? formularioVerificacion(i.intento_id, 'intento') : ''}
    ${puedeRevisar && i.estado === 'REEMBOLSO_EN_VERIFICACION' && i.reembolso_actual_id ? formularioVerificacion(i.reembolso_actual_id, 'reembolso') : ''}
    ${puedeRevisar && i.estado === 'REEMBOLSO_FALLIDO' && i.reembolso_actual_id ? `<button class="secondary" data-retry-refund="${i.reembolso_actual_id}" data-mutate>Reintentar devolución</button>` : ''}</div>`).join('')}`;
  html += reembolsosDePedido(p);
  if (p.avisos.length) html += `<h3 class="sub">Avisos enviados al cliente</h3>${p.avisos.map(a => `<div class="notice">${esc(label(a.tipo))}<br><span class="small">${date(a.fecha)}</span></div>`).join('')}`;
  if (esAdmin() && ultimo && ['INICIADO', 'EN_VERIFICACION'].includes(ultimo.estado)) {
    html += `<div class="form-block"><h3>Pasarela de prueba</h3><p class="small">Simula la notificación de la pasarela (CL-05). Es una herramienta del entorno de demostración, no una verificación manual.</p><button class="secondary" data-callback="${ultimo.intento_id}" data-mutate>Notificar aprobación de prueba</button></div>`;
  }
  const notas = [];
  if (confirmado && !puedeSeparar) notas.push('Separar unidades requiere el perfil Picker en esta tienda.');
  if (p.pagos.some(i => /VERIFICACION|FALLIDO/.test(i.estado)) && !puedeRevisar) notas.push('Verificar pagos y devoluciones requiere el perfil Responsable en esta tienda.');
  if (esAdmin()) notas.push('Administración consulta este pedido en modo lectura.');
  return html + notas.map(n => `<p class="small">${esc(n)}</p>`).join('') + '<p class="small">El estado mostrado proviene del servidor.</p>';
}

function formularioVerificacion(id, tipo) {
  const intento = tipo === 'intento';
  return `<form class="form-block" data-verify="${id}" data-kind="${tipo}"><h3>Verificación del responsable</h3>
    <label>Resultado<select name="resultado"><option value="${intento ? 'APROBADO' : 'CONFIRMADO'}">${intento ? 'Aprobado' : 'Reembolso confirmado'}</option><option value="${intento ? 'RECHAZADO' : 'FALLIDO'}">${intento ? 'Rechazado' : 'Reembolso fallido'}</option></select></label>
    ${intento ? '<label>Fuente consultada<input name="fuente" required minlength="3" value="Consulta manual de pasarela de prueba"></label>' : ''}
    <label>Evidencia de la consulta<textarea name="evidencia" required minlength="3" placeholder="Referencia consultada y resultado comprobado"></textarea></label>
    <button class="primary" data-mutate>Registrar verificación</button></form>`;
}

function tic() {
  const e = $('#countdown');
  if (!e || !estado.detalle) return;
  e.textContent = reloj(segundosRestantes(estado.detalle.t_vence));
}
setInterval(tic, 1000);

async function accion(nombre) {
  if (nombre === 'checkout') { await reservar(); return; }
  if (nombre === 'refresh-detail') { await abrirPedido(estado.pedidoAbierto); return; }
  if (nombre === 'replay') {
    const ultima = Pendientes.ultima;
    if (ultima) await mutacion(null, null, { repetir: ultima });
    await abrirPedido(estado.pedidoAbierto);
    return;
  }
  const p = estado.detalle;
  if (!p) return;
  if (nombre === 'pay') {
    await mutacion(`/v1/pedidos/${p.pedido_id}/intentos-pago`, { monto: p.total_vigente });
    toast('Respuesta de pago recibida. Consulta el estado de tu pedido.');
  }
  if (nombre === 'release') {
    await mutacion(`/v1/reservas/${p.reserva_id}/cierre`, { motivo: 'CANCELACION_CLIENTE' });
    toast('Reserva liberada.');
  }
  if (nombre === 'cancel') {
    await mutacion(`/v1/pedidos/${p.pedido_id}/cancelacion`, { motivo: 'Cancelación solicitada por el cliente' });
    toast('Cancelación registrada. Consulta la devolución.');
  }
  await abrirPedido(p.pedido_id);
  await cargarCatalogo();
}

/* ---------- Operación (interno) ---------- */

const rolesEn = tienda => (Sesion.cuenta?.asignaciones || []).filter(a => a.darkstore_id === tienda).map(a => a.rol);

function tiendasInternas() {
  if (esAdmin()) return estado.tiendas;
  const propias = new Map((Sesion.cuenta?.asignaciones || []).map(a => [a.darkstore_id, a.tienda]));
  return [...propias].map(([darkstore_id, nombre]) => ({ darkstore_id, nombre }));
}

async function prepararTiendasInternas() {
  const tiendas = tiendasInternas();
  if (!tiendas.some(t => t.darkstore_id === estado.opsTienda)) estado.opsTienda = tiendas[0]?.darkstore_id || null;
  sessionStorage.setItem('expressya-ops-tienda', estado.opsTienda || '');
  $('#ops-store').innerHTML = tiendas.map(t => `<option value="${t.darkstore_id}">${esc(t.nombre)}</option>`).join('');
  if (estado.opsTienda) $('#ops-store').value = estado.opsTienda;
}

const AREAS = [
  { id: 'conteos', titulo: 'Conteos y discrepancias', roles: ['PICKER', 'SUPERVISOR', 'PERSONAL_TIENDA'],
    texto: 'Conteo físico (CL-09: Picker o Supervisor), reporte de faltante (CL-10: Picker o Personal de tienda) y resolución con D-05 (CL-12: Supervisor).',
    contratos: 'CL-09, CL-10 y CL-12' },
  { id: 'danos', titulo: 'Daños e inspecciones', roles: ['PERSONAL_TIENDA', 'INSPECTOR', 'AUTORIZADOR'],
    texto: 'Reporte de daño (CL-14: Personal de tienda), inspección (CL-15: Inspector) y reincorporación o desecho con una segunda persona (CL-15: Autorizador).',
    contratos: 'CL-14 y CL-15' },
  { id: 'avisos', titulo: 'Avisos para el responsable', roles: ['RESPONSABLE'],
    texto: 'Alertas del servidor para el responsable de la tienda. Los pagos y devoluciones en verificación se resuelven desde el detalle del pedido (CL-06 y CL-17).',
    contratos: null }
];

async function cargarOperacion() {
  const tienda = estado.opsTienda;
  const roles = rolesEn(tienda);
  $('#ops-roles').innerHTML = esAdmin() ? '<span class="chip">Administrador · lectura</span>' : roles.map(r => `<span class="chip">${esc(label(r))}</span>`).join('');
  if (!tienda) { $('#ops-content').innerHTML = '<div class="empty">Tu cuenta no tiene tiendas asignadas. Pide a administración que revise tus perfiles.</div>'; return; }
  $('#ops-content').innerHTML = '<p class="muted">Consultando la tienda…</p>';
  const [ops, pendientes] = await Promise.all([leer(`/v1/portal/operacion/${tienda}`), leer(`/v1/portal/pendientes/${tienda}`)]);
  if (tienda !== estado.opsTienda) return;
  estado.ops = { ...ops, pendientes };
  pintarOperacion();
}

function pintarOperacion() {
  const { inventario, pedidos, pendientes } = estado.ops;
  const roles = rolesEn(estado.opsTienda);
  const conteo = e => pedidos.filter(p => p.estado === e).length;
  const diferencias = inventario.filter(i => i.e !== i.e_historial || i.n !== i.n_historial).length;
  const abiertas = pendientes.discrepancias.filter(d => d.estado === 'EN_VALIDACION').length;
  const kpis = [
    ['Por preparar', conteo('CONFIRMADO'), 'pedidos confirmados'],
    ['Pendientes de pago', conteo('PENDIENTE_DE_PAGO'), 'con reserva o sin pagar'],
    ['Agotados', inventario.filter(i => i.d <= 0).length, `de ${inventario.length} productos`],
    ['Bloqueados', inventario.filter(i => i.estado_producto === 'BLOQUEADO').length, 'productos'],
    ['Discrepancias abiertas', abiertas, 'en validación'],
    ['Diferencias de historial', diferencias, diferencias ? 'revisar movimientos' : 'saldos cuadrados']
  ];
  const filtros = ['TODOS', 'PENDIENTE_DE_PAGO', 'CONFIRMADO', 'SIN_COMPRA', 'CANCELADO'];
  const lista = pedidos.filter(p => (estado.opsFiltro === 'TODOS' || p.estado === estado.opsFiltro) && (!estado.opsBusqueda || p.pedido_id.toUpperCase().includes(estado.opsBusqueda.toUpperCase())));
  const areas = AREAS.filter(a => esAdmin() || a.roles.some(r => roles.includes(r)));
  $('#ops-content').innerHTML = `
    <section class="kpis">${kpis.map(([t, v, s]) => `<div class="kpi"><span class="small">${t}</span><strong>${v}</strong><span class="small">${s}</span></div>`).join('')}</section>
    <section class="panel"><h2>Inventario en tiempo real.</h2>
      <p class="small">Existencias − Reservadas − Comprometidas = Disponibles. Las cifras internas solo se consultan con un perfil asignado a la tienda.</p>
      <div class="table-scroll"><table><thead><tr><th>Producto</th><th>Existencias</th><th>No vendibles</th><th>Reservadas</th><th>Comprometidas</th><th>Disponibles</th><th>Historial</th></tr></thead><tbody>
      ${inventario.map(i => `<tr><td>${esc(i.nombre)}<br><span class="small">${esc(label(i.estado_producto))}</span></td><td>${i.e}</td><td>${i.n}</td><td>${i.r}</td><td>${i.c}</td><td>${i.d}</td>
        <td><button class="link" data-history="${i.producto_id}">${i.e === i.e_historial && i.n === i.n_historial ? 'Ver movimientos' : 'Revisar diferencia'}</button></td></tr>`).join('')}
      </tbody></table></div></section>
    <section class="panel"><div class="row wrap-row"><h2>Pedidos de la tienda.</h2><input id="ops-search" type="search" placeholder="Buscar por número de pedido" value="${esc(estado.opsBusqueda)}" aria-label="Buscar pedido"></div>
      <div class="pills" style="margin-bottom:18px">${filtros.map(f => `<button data-ops-filter="${f}" class="${f === estado.opsFiltro ? 'selected' : ''}">${f === 'TODOS' ? 'Todos' : esc(label(f))}</button>`).join('')}</div>
      <div class="order-list">${lista.map(tarjetaPedido).join('') || '<div class="empty">No hay pedidos con este filtro.</div>'}</div></section>
    ${areas.map(a => areaDeRol(a, pendientes, roles)).join('')}`;
}

function opcionesProducto() {
  return estado.ops.inventario.map(i => `<option value="${i.producto_id}">${esc(i.nombre)}</option>`).join('');
}

function formulario(op, titulo, campos, boton) {
  return `<form class="form-block" data-op="${op}"><h3>${esc(titulo)}</h3>${campos}<button class="primary" data-mutate>${esc(boton)}</button></form>`;
}

function formulariosDeConteo(pendientes, roles) {
  let html = '';
  if (roles.some(r => ['PICKER', 'SUPERVISOR'].includes(r))) {
    html += formulario('conteo', 'Registrar conteo físico (CL-09)',
      `<label>Producto<select name="producto_id">${opcionesProducto()}</select></label><label>Unidades contadas en el anaquel<input name="cantidad_contada" type="number" min="0" required></label>`,
      'Registrar conteo');
  }
  if (roles.includes('SUPERVISOR')) {
    pendientes.discrepancias.filter(d => d.estado === 'EN_VALIDACION').forEach(d => {
      const conteos = pendientes.conteos.filter(c => c.producto_id === d.producto_id);
      html += formulario(`resolucion:${d.discrepancia_id}`, `Resolver discrepancia de ${d.nombre} (CL-12, D-05)`,
        conteos.length
          ? `<label>Conteo que respalda la resolución<select name="conteo_id">${conteos.map(c => `<option value="${c.conteo_id}">${c.cantidad_contada} contadas · ${date(c.fecha_registro)}</option>`).join('')}</select></label>
             <label>Unidades físicas validadas<input name="unidades_validadas" type="number" min="0" required></label>
             <label>Evidencia<input name="evidencia" required minlength="3" placeholder="Recuento con foto del anaquel"></label>
             <label>Motivo<input name="motivo" required minlength="3" value="Conciliación del conteo"></label>`
          : '<p class="small">Primero registra un conteo de este producto.</p>',
        'Resolver y aplicar D-05');
    });
  }
  return html;
}

function formulariosDeDanos(pendientes, roles) {
  let html = '';
  if (roles.includes('PERSONAL_TIENDA')) {
    html += formulario('dano', 'Reportar daño (CL-14)',
      `<label>Producto<select name="producto_id">${opcionesProducto()}</select></label><label>Unidades dañadas<input name="cantidad" type="number" min="1" required></label><label>Motivo<input name="motivo" required minlength="3" placeholder="Caja aplastada al descargar"></label>`,
      'Reportar daño');
  }
  if (roles.includes('INSPECTOR')) {
    pendientes.danos.filter(d => d.por_inspeccionar > 0).forEach(d => {
      html += formulario(`inspeccion:${d.movimiento_id}`, `Inspeccionar daño de ${d.nombre} (CL-15)`,
        `<label>Unidades<input name="cantidad" type="number" min="1" max="${d.por_inspeccionar}" value="${d.por_inspeccionar}" required></label>
         <label>Resultado<select name="resultado"><option value="APTA">Apta: se puede vender</option><option value="NO_APTA">No apta</option></select></label>
         <label>Evidencia<input name="evidencia" required minlength="3" placeholder="Foto de la prenda revisada"></label>`,
        'Registrar inspección');
    });
  }
  if (roles.includes('AUTORIZADOR')) {
    pendientes.inspecciones.filter(i => !i.resuelta).forEach(i => {
      const accion = i.resultado === 'APTA' ? 'REINCORPORAR' : 'DESECHAR';
      html += formulario(`autorizacion:${i.inspeccion_id}`, `${i.resultado === 'APTA' ? 'Reincorporar' : 'Desechar'} ${i.cantidad} de ${i.nombre} (CL-15)`,
        `<input type="hidden" name="accion" value="${accion}"><p class="small">Debe autorizar una persona distinta de quien inspeccionó.</p>`,
        i.resultado === 'APTA' ? 'Autorizar reincorporación' : 'Autorizar desecho');
    });
  }
  return html;
}

function areaDeRol(area, pendientes, roles) {
  const tuyo = area.roles.filter(r => roles.includes(r)).map(label).join(', ');
  let cola = '';
  if (area.id === 'conteos') {
    cola = formulariosDeConteo(pendientes, roles)
      + tabla('Conteos registrados', ['Producto', 'Contadas', 'Sistema al corte', 'Fecha'], pendientes.conteos.map(c => [esc(c.nombre), c.cantidad_contada, c.e_al_corte, date(c.fecha_registro)]))
      + tabla('Discrepancias', ['Producto', 'Origen', 'Estado', 'Detectada'], pendientes.discrepancias.map(d => [esc(d.nombre), esc(label(d.origen)), badge(d.estado), date(d.fecha_deteccion)]));
  }
  if (area.id === 'danos') {
    cola = formulariosDeDanos(pendientes, roles)
      + tabla('Daños reportados', ['Producto', 'Unidades', 'Por inspeccionar', 'Motivo', 'Fecha'], pendientes.danos.map(d => [esc(d.nombre), d.cantidad, d.por_inspeccionar, esc(d.motivo), date(d.fecha)]))
      + tabla('Inspecciones', ['Producto', 'Unidades', 'Resultado', 'Estado', 'Fecha'], pendientes.inspecciones.map(i => [esc(i.nombre), i.cantidad, badge(i.resultado), i.resuelta ? 'Resuelta' : 'Por autorizar', date(i.fecha)]));
  }
  if (area.id === 'avisos') {
    cola = tabla('Avisos', ['Tipo', 'Pedido', 'Fecha', 'Entrega'], pendientes.avisos.map(a => [esc(label(a.tipo)), a.pedido_id ? `<button class="link" data-order="${a.pedido_id}">${short(a.pedido_id)}</button>` : '—', date(a.fecha), a.entregado_en ? 'Entregado' : 'Pendiente']));
  }
  return `<section class="panel"><div class="row"><h2>${esc(area.titulo)}.</h2>${tuyo ? `<span class="chip">${esc(tuyo)}</span>` : '<span class="chip">Lectura</span>'}</div>
    <p class="small">${esc(area.texto)}</p>
    ${cola}</section>`;
}

const OPERACIONES = {
  conteo: (d, tienda) => [`/v1/darkstores/${tienda}/productos/${d.producto_id}/conteos`, { cantidad_contada: Number(d.cantidad_contada), fecha_corte: new Date().toISOString() }],
  resolucion: (d, _, id) => [`/v1/discrepancias/${id}/resolucion`, { conteo_id: d.conteo_id, unidades_validadas: Number(d.unidades_validadas), evidencia: d.evidencia, motivo: d.motivo }],
  dano: (d, tienda) => [`/v1/darkstores/${tienda}/productos/${d.producto_id}/danos`, { cantidad: Number(d.cantidad), motivo: d.motivo }],
  inspeccion: (d, _, id) => [`/v1/danos/${id}/inspecciones`, { cantidad: Number(d.cantidad), resultado: d.resultado, evidencia: d.evidencia }],
  autorizacion: (d, _, id) => [`/v1/inspecciones/${id}/resolucion`, { accion: d.accion }],
  faltante: (d, _, id) => [`/v1/lineas-pedido/${id}/faltantes`, { evidencia: d.evidencia }]
};

function tabla(titulo, columnas, filas) {
  return `<h3 class="sub">${esc(titulo)}</h3>${filas.length ? `<div class="table-scroll"><table><thead><tr>${columnas.map(c => `<th>${c}</th>`).join('')}</tr></thead><tbody>${filas.map(f => `<tr>${f.map(c => `<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table></div>` : '<p class="small">Sin registros por ahora.</p>'}`;
}

async function historial(productoId) {
  estado.pedidoAbierto = null;
  estado.detalle = null;
  const filas = await leer(`/v1/portal/movimientos/${estado.opsTienda}/${productoId}`);
  const nombre = estado.ops?.inventario.find(i => i.producto_id === productoId)?.nombre || '';
  abrirPanel('TRAZABILIDAD', `<h2>Cada cambio cuenta.</h2><p class="muted">${esc(nombre)} · movimientos guardados en el servidor.</p>
    <div class="table-scroll"><table class="history"><thead><tr><th>Movimiento</th><th>Saldo</th><th>Cambio</th></tr></thead><tbody>
    ${filas.map(r => `<tr><td><strong>${esc(label(r.tipo))}</strong><br><span class="small">${date(r.fecha)}<br>${esc(r.motivo)}<br>${r.usuario_id ? 'Usuario ' + short(r.usuario_id) : 'Operación de cliente o sistema'}${r.autorizado_por ? ' · autorizó ' + short(r.autorizado_por) : ''}</span></td><td>${esc(r.saldo || '—')}</td><td>${r.cantidad > 0 ? '+' : ''}${r.cantidad ?? '—'}</td></tr>`).join('') || '<tr><td colspan="3">Sin movimientos.</td></tr>'}
    </tbody></table></div>`);
}

/* ---------- Administración ---------- */

async function cargarCuentas() {
  estado.cuentas = await leer('/v1/admin/cuentas');
  pintarCuentas();
  if (!$('#alta-filas').children.length) agregarFilaPerfil();
}

function pintarCuentas() {
  const q = ($('#accounts-filter').value || '').toLowerCase();
  const filas = estado.cuentas.filter(c => !q || [c.usuario, c.nombre, label(c.tipo), ...c.asignaciones.map(a => label(a.rol) + ' ' + a.tienda)].join(' ').toLowerCase().includes(q));
  $('#accounts').innerHTML = `<table><thead><tr><th>Cuenta</th><th>Tipo</th><th>Perfiles</th><th>Estado</th><th></th></tr></thead><tbody>${filas.map(c => `<tr>
    <td><strong>${esc(c.nombre)}</strong><br><span class="small">${esc(c.usuario)} · desde ${date(c.creado_en)}</span></td>
    <td>${esc(label(c.tipo))}</td>
    <td class="wrap-cell">${c.asignaciones.map(a => `<span class="chip">${esc(label(a.rol))} · ${esc(a.tienda)}</span>`).join('') || '<span class="small">—</span>'}</td>
    <td>${c.activo ? badge('ACTIVA') : '<span class="badge neutral">Desactivada</span>'}</td>
    <td>${c.tipo === 'ADMINISTRADOR' || c.cuenta_id === Sesion.cuenta.cuenta_id ? '<span class="small">—</span>' : `<button class="link" data-toggle-account="${c.cuenta_id}" data-activo="${!c.activo}">${c.activo ? 'Desactivar' : 'Activar'}</button>`}</td>
  </tr>`).join('') || '<tr><td colspan="5">Sin cuentas que coincidan.</td></tr>'}</tbody></table>`;
}

function agregarFilaPerfil() {
  const fila = document.createElement('div');
  fila.className = 'assignment-row';
  fila.innerHTML = `<select name="tienda" aria-label="Tienda">${estado.tiendas.map(t => `<option value="${t.darkstore_id}">${esc(t.nombre)}</option>`).join('')}</select>
    <select name="rol" aria-label="Perfil">${ROLES.map(r => `<option value="${r}">${esc(label(r))}</option>`).join('')}</select>
    <button type="button" class="icon-button" data-remove-row aria-label="Quitar perfil">×</button>`;
  $('#alta-filas').append(fila);
}

function ajustarAltaPorTipo() {
  const interno = $('#form-alta').tipo.value === 'INTERNO';
  $('#alta-asignaciones').hidden = !interno;
}

$('#form-alta').addEventListener('submit', async ev => {
  ev.preventDefault();
  const f = ev.target, boton = f.querySelector('button.primary');
  const tipo = f.tipo.value;
  const asignaciones = tipo === 'INTERNO' ? $$('#alta-filas .assignment-row').map(r => ({ darkstore_id: r.querySelector('[name=tienda]').value, rol: r.querySelector('[name=rol]').value })) : [];
  const unicas = [...new Map(asignaciones.map(a => [a.darkstore_id + a.rol, a])).values()];
  if (tipo === 'INTERNO' && !unicas.length) { toast('Asigna al menos una tienda y un perfil.', true); return; }
  boton.disabled = true;
  try {
    await api('/v1/admin/cuentas', { method: 'POST', body: { usuario: f.usuario.value.trim(), nombre: f.nombre.value.trim(), clave: f.clave.value, tipo, asignaciones: unicas } });
    f.reset();
    $('#alta-filas').innerHTML = '';
    agregarFilaPerfil();
    ajustarAltaPorTipo();
    toast('Cuenta creada.');
    await cargarCuentas();
  } catch (e) { toast(mensajeError(e), true); } finally { boton.disabled = false; }
});
$('#form-alta').tipo.addEventListener('change', ajustarAltaPorTipo);
$('#alta-agregar').onclick = agregarFilaPerfil;
$('#accounts-filter').addEventListener('input', pintarCuentas);

async function cargarAuditoria() {
  const filas = await leer('/v1/admin/auditoria');
  $('#audit').innerHTML = `<table class="history"><thead><tr><th>Acción</th><th>Cuenta</th><th>Realizó</th><th>Detalle</th><th>Fecha</th></tr></thead><tbody>${filas.map(a => `<tr>
    <td>${esc(label(a.accion))}</td><td>${esc(a.cuenta)}</td><td>${esc(a.actor || 'Sistema o registro propio')}</td>
    <td class="small">${esc(describirDetalle(a.detalle))}</td><td>${date(a.fecha)}</td></tr>`).join('') || '<tr><td colspan="5">Sin registros.</td></tr>'}</tbody></table>`;
}

function describirDetalle(d) {
  if (!d) return '';
  if ('activo' in d) return d.activo ? 'Acceso activado' : 'Acceso desactivado';
  const tiendas = new Map(estado.tiendas.map(t => [t.darkstore_id, t.nombre]));
  const perfiles = (d.asignaciones || []).map(a => `${label(a.rol)} · ${tiendas.get(a.darkstore_id) || short(a.darkstore_id)}`).join(', ');
  return [d.tipo ? label(d.tipo) : '', perfiles].filter(Boolean).join(' — ');
}

$('#settings-open').onclick = async () => {
  try {
    const modo = await leer('/simulador/modo');
    $('#pay-mode').value = modo.cobro;
    $('#refund-mode').value = modo.reembolso;
    $('#settings').showModal();
  } catch (e) { toast(mensajeError(e), true); }
};
$('#save-modes').onclick = async () => {
  try {
    await api('/simulador/modo', { method: 'POST', body: { cobro: $('#pay-mode').value, reembolso: $('#refund-mode').value } });
    toast('Pasarela de prueba configurada.');
    $('#settings').close();
  } catch (e) { toast(mensajeError(e), true); }
};

/* ---------- Eventos ---------- */

async function refrescarVistaActual() {
  if (estado.vista === 'pedidos') await cargarPedidos();
  if (estado.vista === 'operacion') await cargarOperacion();
  if (estado.vista === 'tienda') await cargarCatalogo();
}

document.addEventListener('click', async ev => {
  const b = ev.target.closest('button,a[data-view]');
  if (!b || b.type === 'submit' && b.form) return;
  try {
    if (b.dataset.close) { $('#' + b.dataset.close).close(); return; }
    if (b.dataset.view) { ev.preventDefault(); $$('dialog[open]').forEach(d => d.close()); await cambiarVista(b.dataset.view); return; }
    if (b.dataset.tab) { mostrarPestana(b.dataset.tab); $('#acceso-error').hidden = true; return; }
    if (b.dataset.filter) { estado.filtro = b.dataset.filter; $$('[data-filter]').forEach(e => e.classList.toggle('selected', e === b)); pintarCatalogo(); return; }
    if (b.dataset.opsFilter) { estado.opsFiltro = b.dataset.opsFilter; pintarOperacion(); return; }
    if (b.dataset.add) {
      const p = estado.catalogo.find(x => x.producto_id === b.dataset.add), previo = estado.bolsa.find(x => x.producto_id === p.producto_id);
      if (previo) previo.qty++; else estado.bolsa.push({ producto_id: p.producto_id, nombre: p.nombre, precio: p.precio, qty: 1 });
      guardarBolsa();
      toast(`${p.nombre} se agregó a tu bolsa.`);
      return;
    }
    if (b.dataset.qty) {
      const p = estado.bolsa.find(x => x.producto_id === b.dataset.qty);
      p.qty += Number(b.dataset.delta);
      estado.bolsa = estado.bolsa.filter(x => x.qty > 0);
      guardarBolsa();
      pintarBolsa();
      return;
    }
    if (b.dataset.order) { await abrirPedido(b.dataset.order); return; }
    if (b.dataset.history) { await historial(b.dataset.history); return; }
    if (b.dataset.action) { await accion(b.dataset.action); return; }
    if (b.dataset.removeRow) { b.closest('.assignment-row').remove(); return; }
    if (b.dataset.toggleAccount) {
      b.disabled = true;
      await api(`/v1/admin/cuentas/${b.dataset.toggleAccount}`, { method: 'PATCH', body: { activo: b.dataset.activo === 'true' } });
      toast('Acceso actualizado.');
      await cargarCuentas();
      return;
    }
    if (b.dataset.retryRefund) {
      await mutacion(`/v1/reembolsos/${b.dataset.retryRefund}/reintentos`, {});
      toast('Devolución reenviada a la pasarela.');
      await abrirPedido(estado.pedidoAbierto);
      return;
    }
    if (b.dataset.callback) {
      await mutacion('/v1/pasarela/notificaciones', { objeto: 'INTENTO', objeto_id: b.dataset.callback, referencia_externa: 'DEMO-' + b.dataset.callback, resultado: 'APROBADO' });
      toast('Notificación de prueba enviada a la pasarela.');
      await abrirPedido(estado.pedidoAbierto);
    }
  } catch (e) {
    if (e.status !== 401) toast(mensajeError(e), true);
    if (b.dataset.toggleAccount) b.disabled = false;
  }
});

document.addEventListener('submit', async ev => {
  const f = ev.target;
  if (f.matches('[data-op]')) {
    ev.preventDefault();
    const [tipo, id] = f.dataset.op.split(':');
    try {
      const [ruta, cuerpo] = OPERACIONES[tipo](Object.fromEntries(new FormData(f)), estado.opsTienda, id);
      await mutacion(ruta, cuerpo);
      toast('Operación registrada en el servidor.');
      if (estado.pedidoAbierto) await abrirPedido(estado.pedidoAbierto);
      await cargarOperacion();
    } catch (e) { if (e.status !== 401) toast(mensajeError(e), true); }
    return;
  }
  if (!f.matches('[data-separate],[data-verify]')) return;
  ev.preventDefault();
  try {
    const d = Object.fromEntries(new FormData(f));
    if (f.dataset.separate) {
      await mutacion(`/v1/lineas-pedido/${f.dataset.separate}/separaciones`, { cantidad: Number(d.cantidad) });
    } else if (f.dataset.kind === 'intento') {
      await mutacion(`/v1/intentos-pago/${f.dataset.verify}/verificacion`, { resultado: d.resultado, evidencia_fuente: d.fuente, evidencia: { observacion: d.evidencia } });
    } else {
      await mutacion(`/v1/reembolsos/${f.dataset.verify}/verificacion`, { resultado: d.resultado, evidencia: { observacion: d.evidencia } });
    }
    toast('Operación registrada en el servidor.');
    await abrirPedido(estado.pedidoAbierto);
    await cargarOperacion();
  } catch (e) { if (e.status !== 401) toast(mensajeError(e), true); }
});

document.addEventListener('input', ev => {
  if (ev.target.id === 'ops-search') {
    estado.opsBusqueda = ev.target.value.trim();
    const pos = ev.target.selectionStart;
    pintarOperacion();
    const nuevo = $('#ops-search');
    nuevo.focus();
    nuevo.setSelectionRange(pos, pos);
  }
});

$('#bag').onclick = pintarBolsa;
$('#store').onchange = async () => {
  if (estado.bolsa.length) { $('#store').value = estado.tienda; toast('Vacía la bolsa antes de cambiar de tienda.', true); return; }
  estado.tienda = $('#store').value;
  sessionStorage.setItem('expressya-tienda', estado.tienda);
  estado.catalogo = [];
  $('#products').innerHTML = '<p>Consultando la tienda…</p>';
  try { await cargarCatalogo(); } catch (e) { toast(mensajeError(e), true); }
};
$('#ops-store').onchange = () => {
  estado.opsTienda = $('#ops-store').value;
  sessionStorage.setItem('expressya-ops-tienda', estado.opsTienda);
  estado.opsFiltro = 'TODOS';
  estado.opsBusqueda = '';
  cargarOperacion().catch(e => { if (e.status !== 401) toast(mensajeError(e), true); });
};
$('#refresh-orders').onclick = () => cargarPedidos().catch(e => toast(mensajeError(e), true));
$('#refresh-ops').onclick = () => cargarOperacion().catch(e => toast(mensajeError(e), true));
$('#refresh-accounts').onclick = () => cargarCuentas().catch(e => toast(mensajeError(e), true));
$('#refresh-audit').onclick = () => cargarAuditoria().catch(e => toast(mensajeError(e), true));
$('#drawer').addEventListener('close', () => { estado.pedidoAbierto = null; estado.detalle = null; });
$('#acceso').addEventListener('close', () => { if (!Sesion.cuenta) estado.despuesDeEntrar = null; });

setInterval(async () => {
  if (document.hidden || Pendientes.ocupado || !estado.pedidoAbierto || !$('#drawer').open || !Sesion.cuenta) return;
  try {
    const p = await leer(`/v1/portal/pedido/${estado.pedidoAbierto}`);
    if (JSON.stringify({ ...p, hora_servidor: null }) !== JSON.stringify({ ...estado.detalle, hora_servidor: null })) {
      estado.detalle = p;
      refrescarPanel(PORTAL === 'interno' ? detalleInterno(p) : detalleCliente(p));
      tic();
    }
  } catch (e) {
    $('#connection').textContent = 'No se pudo actualizar el estado; vuelve a intentarlo';
  }
}, 4000);

async function iniciar() {
  aplicarPermisos();
  try {
    await sincronizarReloj();
    await cargarSesion();
    aplicarPermisos();
    estado.tiendas = await leer('/v1/portal/tiendas');
    if (!estado.tiendas.some(s => s.darkstore_id === estado.tienda)) estado.tienda = estado.tiendas.find(s => s.nombre === 'Dark Store Norte')?.darkstore_id || estado.tiendas[0]?.darkstore_id;
    sessionStorage.setItem('expressya-tienda', estado.tienda);
    $('#store').innerHTML = estado.tiendas.map(s => `<option value="${s.darkstore_id}">${esc(s.nombre)}</option>`).join('');
    $('#store').value = estado.tienda;
    guardarBolsa();
    if (PORTAL === 'cliente') await cargarCatalogo();
    if (PORTAL === 'interno' && Sesion.cuenta) await prepararTiendasInternas();
    await cambiarVista(vistaInicial());
    $('#connection').textContent = 'Conectado al backend · PostgreSQL';
    if (Pendientes.pendiente) toast('Hay una operación sin respuesta. Recupérala antes de continuar.', true);
  } catch (e) {
    $('#connection').textContent = 'Conexión no disponible';
    $('#products').innerHTML = '<div class="empty"><h3>No pudimos conectar.</h3><p>Comprueba que el servidor y PostgreSQL estén encendidos.</p><button class="primary" onclick="location.reload()">Reintentar conexión</button></div>';
    toast(mensajeError(e), true);
  }
}
iniciar();
