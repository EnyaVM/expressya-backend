'use strict';

const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const money = v => new Intl.NumberFormat('es-MX', { style: 'currency', currency: 'MXN' }).format(Number(v));
const date = v => v ? new Date(v).toLocaleString('es-MX', { dateStyle: 'medium', timeStyle: 'short' }) : '—';
const short = id => String(id ?? '').slice(0, 8).toUpperCase();

const ETIQUETAS = {
  PENDIENTE_DE_PAGO: 'Pendiente de pago', CONFIRMADO: 'Pedido confirmado', CANCELADO: 'Pedido cancelado',
  SIN_COMPRA: 'Compra no completada', ACTIVA: 'Reserva activa', EXPIRADA: 'Reserva vencida',
  LIBERADA: 'Reserva liberada', COMPROMETIDA: 'Reserva confirmada', APROBADO: 'Pago aprobado',
  RECHAZADO: 'Pago rechazado', INICIADO: 'Procesando pago', EN_VERIFICACION: 'Pago en verificación',
  REEMBOLSO_EN_PROCESO: 'Devolución en proceso', REEMBOLSO_EN_VERIFICACION: 'Devolución en verificación',
  REEMBOLSO_FALLIDO: 'Devolución fallida', REEMBOLSADO_TOTAL: 'Devolución completa',
  REEMBOLSADO_PARCIAL: 'Devolución parcial', ASIGNADA: 'Por preparar', SEPARADA: 'Preparada',
  PARCIAL: 'Preparada parcialmente', NO_SURTIBLE: 'Faltante por resolver', CANCELADA: 'Cancelada',
  HABILITADO: 'Habilitado', BLOQUEADO: 'Bloqueado', EN_VALIDACION: 'En validación', RESUELTA: 'Resuelta',
  PEDIDO_CANCELADO: 'Tu pedido se canceló', CANCELACION_PARCIAL: 'Se canceló parte de tu pedido',
  CANCELACION_CLIENTE: 'Cancelación del cliente', CANCELACION_POR_FALTANTE: 'Cancelación por faltante',
  PAGO_TARDIO: 'Pago recibido después del plazo', COBRO_DUPLICADO: 'Cobro duplicado',
  PICKER: 'Picker', PERSONAL_TIENDA: 'Personal de tienda', SUPERVISOR: 'Supervisor', INSPECTOR: 'Inspector',
  AUTORIZADOR: 'Autorizador', RESPONSABLE: 'Responsable', ADMINISTRADOR: 'Administrador',
  INTERNO: 'Personal', CLIENTE: 'Cliente', APTA: 'Apta', NO_APTA: 'No apta',
  DANO: 'Daño', REINCORPORACION: 'Reincorporación', DESECHO: 'Desecho', PRODUCTO_RETIRADO: 'Producto retirado por faltante',
  CONTEO: 'Conteo físico', REPORTE_PICKER: 'Reporte del picker', FALTANTE: 'Faltante', DIFERENCIA_SIN_FALTANTE: 'Diferencia sin faltante',
  DISCREPANCIA: 'Discrepancia', RESOLUCION: 'Resolución', RETIRO_RESERVA: 'Retiro de reserva', SEPARACION: 'Separación',
  ALTA_CUENTA: 'Alta de cuenta', CAMBIO_ACCESO: 'Cambio de acceso', EVENTO_SIN_PROCESAR: 'Evento sin procesar'
};
const label = s => ETIQUETAS[s] || String(s ?? '').replaceAll('_', ' ').toLowerCase();
const badge = s => {
  const tono = /PENDIENTE|VERIFICACION|INICIADO|PROCESO|EN_VALIDACION|NO_SURTIBLE/.test(s) ? 'pending'
    : /CANCELAD|RECHAZAD|SIN_COMPRA|EXPIRADA|LIBERADA|BLOQUEADO|FALLIDO|NO_APTA/.test(s) ? 'neutral' : '';
  return `<span class="badge ${tono}">${esc(label(s))}</span>`;
};

let toastTimer;
function toast(mensaje, error = false) {
  const e = $('#toast');
  ($('dialog[open]') || document.body).append(e);
  e.textContent = mensaje;
  e.className = error ? 'error' : '';
  e.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { e.hidden = true; }, 7000);
}
document.addEventListener('close', ev => {
  if (ev.target.tagName === 'DIALOG' && ev.target.contains($('#toast'))) {
    $('#toast').hidden = true;
    document.body.append($('#toast'));
  }
}, true);

const mensajeError = e => `${e.message}${e.code ? ' · ' + e.code : ''}`;

class ErrorApi extends Error {
  constructor(mensaje, codigo, estado, detalle) {
    super(mensaje);
    this.code = codigo;
    this.status = estado;
    this.detalle = detalle;
  }
}

function ventanaDeEstaPestana() {
  let ventana = sessionStorage.getItem('expressya-ventana');
  if (!ventana) {
    ventana = crypto.randomUUID();
    sessionStorage.setItem('expressya-ventana', ventana);
  }
  return ventana;
}

const Sesion = {
  portal: document.documentElement.dataset.portal,
  ventana: ventanaDeEstaPestana(),
  cuenta: null,
  alPerderla: () => {},
  clave(tipo) {
    return this.cuenta ? `expressya:${tipo}:${this.portal}:${this.cuenta.cuenta_id}` : null;
  }
};

async function api(path, { method = 'GET', body, clave } = {}) {
  const headers = { 'Content-Type': 'application/json', 'X-Portal': Sesion.portal, 'X-Ventana': Sesion.ventana };
  if (method !== 'GET' && Sesion.cuenta?.csrf) headers['X-CSRF-Token'] = Sesion.cuenta.csrf;
  if (clave) headers['Clave-Operacion'] = clave;
  let res;
  try {
    res = await fetch(path, { method, headers, credentials: 'same-origin', ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  } catch (e) {
    throw new ErrorApi('No hubo respuesta del servidor. Revisa la conexión.', 'SIN_RESPUESTA', 0);
  }
  const texto = await res.text();
  let datos = null;
  try { datos = texto ? JSON.parse(texto) : null; } catch { datos = null; }
  if (!res.ok) {
    const error = new ErrorApi(datos?.mensaje || 'No se pudo completar la operación.', datos?.codigo, res.status, datos?.detalle);
    if (res.status === 401 && Sesion.cuenta && !path.startsWith('/v1/auth/login')) {
      Sesion.cuenta = null;
      Sesion.alPerderla(error);
    }
    throw error;
  }
  return { datos, previo: res.headers.get('Resultado-Previo') === 'true' };
}

const leer = async path => (await api(path)).datos;

async function cargarSesion() {
  try {
    Sesion.cuenta = await leer('/v1/auth/me');
  } catch (e) {
    if (e.status !== 401 && e.status !== 403) throw e;
    Sesion.cuenta = null;
  }
  return Sesion.cuenta;
}

async function iniciarSesion(usuario, clave) {
  const { datos } = await api('/v1/auth/login', { method: 'POST', body: { usuario, clave, portal: Sesion.portal } });
  Sesion.cuenta = datos;
  return datos;
}

async function cerrarSesion() {
  try {
    if (Sesion.cuenta) await api('/v1/auth/logout', { method: 'POST', body: {} });
  } finally {
    Sesion.cuenta = null;
  }
}

const Pendientes = {
  ocupado: false,
  leer(tipo) {
    const k = Sesion.clave(tipo);
    return k ? JSON.parse(sessionStorage.getItem(k) || 'null') : null;
  },
  guardar(tipo, valor) {
    const k = Sesion.clave(tipo);
    if (!k) return;
    if (valor) sessionStorage.setItem(k, JSON.stringify(valor));
    else sessionStorage.removeItem(k);
  },
  get pendiente() { return this.leer('pendiente'); },
  get ultima() { return this.leer('ultima'); },
  alCambiar: () => {}
};

async function mutacion(path, body, { repetir = null } = {}) {
  if (Pendientes.ocupado) return null;
  if (!Sesion.cuenta) throw new ErrorApi('Inicia sesión para continuar.', 'NO_AUTENTICADO', 401);
  const identidad = JSON.stringify({ path, body });
  const pendiente = Pendientes.pendiente;
  let solicitud;
  if (repetir) solicitud = repetir;
  else if (pendiente && pendiente.identidad === identidad) solicitud = pendiente;
  else if (pendiente) throw new ErrorApi('Hay una operación sin respuesta. Recupérala antes de iniciar otra.', 'OPERACION_PENDIENTE', 409);
  else solicitud = { path, body, clave: crypto.randomUUID(), identidad, cuenta_id: Sesion.cuenta.cuenta_id, portal: Sesion.portal };
  if (solicitud.cuenta_id !== Sesion.cuenta.cuenta_id || solicitud.portal !== Sesion.portal) {
    throw new ErrorApi('Esa operación pertenece a otra sesión.', 'SIN_PERMISO', 403);
  }
  const clavePendiente = Sesion.clave('pendiente');
  Pendientes.ocupado = true;
  Pendientes.guardar('pendiente', solicitud);
  Pendientes.alCambiar();
  $$('[data-mutate]').forEach(b => { b.disabled = true; });
  try {
    const { datos, previo } = await api(solicitud.path, { method: 'POST', body: solicitud.body, clave: solicitud.clave });
    Pendientes.guardar('ultima', solicitud);
    Pendientes.guardar('pendiente', null);
    if (previo) toast('Recuperamos la respuesta original de esta operación.');
    return datos;
  } catch (e) {
    if (e.status && e.status < 500) sessionStorage.removeItem(clavePendiente);
    throw e;
  } finally {
    Pendientes.ocupado = false;
    Pendientes.alCambiar();
    $$('[data-mutate]').forEach(b => { b.disabled = false; });
  }
}

const recuperarPendiente = () => {
  const p = Pendientes.pendiente;
  return p ? mutacion(null, null, { repetir: p }).then(datos => ({ datos, solicitud: p })) : Promise.resolve(null);
};

let desfaseServidor = 0;
async function sincronizarReloj() {
  const salud = await leer('/v1/salud');
  desfaseServidor = new Date(salud.hora_servidor) - Date.now();
}
const segundosRestantes = tVence => Math.max(0, Math.ceil((new Date(tVence) - Date.now() - desfaseServidor) / 1000));
const reloj = s => s ? `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}` : 'Plazo cumplido';

function abrirPanel(kicker, html) {
  $('#drawer-kicker').textContent = kicker;
  $('#drawer-content').innerHTML = html;
  if (!$('#drawer').open) $('#drawer').showModal();
}

function refrescarPanel(html) {
  const activo = $('#drawer').contains(document.activeElement) && /INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName);
  if (activo) return false;
  const scroll = $('#drawer').scrollTop;
  $('#drawer-content').innerHTML = html;
  $('#drawer').scrollTop = scroll;
  return true;
}

function lineasDePedido(p, extra = () => '') {
  return `<div class="detail-lines">${p.lineas.map(l => `<div class="detail-line">
    <div class="row"><strong>${esc(l.nombre)}</strong><span>${money(Number(l.precio) * l.cantidad)}</span></div>
    <p class="small">${l.cantidad} ${l.cantidad === 1 ? 'unidad' : 'unidades'} · ${money(l.precio)} cada una${l.fecha_retiro ? ' · Retirada de la reserva' : ''}</p>
    ${l.linea_id ? `<p class="small">${l.s} preparadas · ${l.k} canceladas · ${esc(label(l.estado))}</p>` : ''}
    ${extra(l)}
  </div>`).join('')}</div>`;
}

function reembolsosDePedido(p) {
  if (!p.reembolsos.length) return '';
  return `<h3 class="sub">Devoluciones</h3>${p.reembolsos.map(r => `<div class="detail-line">
    <div class="row"><strong>${money(r.monto)}</strong><span>${r.fecha_confirmacion ? 'Confirmada' : r.fecha_fallo_confirmado ? 'Fallida' : r.fecha_envio ? 'Enviada, sin confirmar' : 'Pendiente de envío'}</span></div>
    <p class="small">${esc(label(r.motivo))} · ${date(r.fecha_solicitud)}</p></div>`).join('')}`;
}

function cuadroPendiente(idBoton) {
  return `<div class="notice warn pending-box" hidden><p>Una operación quedó sin respuesta. Recupera su resultado con la misma clave antes de iniciar otra.</p><button id="${idBoton}" class="secondary" data-mutate>Recuperar operación pendiente</button></div>`;
}
