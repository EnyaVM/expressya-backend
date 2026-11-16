-- ExpressYa · Equipo 03 · Semana 3 · Modelo físico (PostgreSQL 16)
-- 01_modelo_fisico_expressya.sql — esquema expressya, 24 tablas, 3 vistas (v1.1, 5 nov 2026)
-- Ejecutar una sola vez, en una base vacía, con la cuenta de migración:
--   psql -X --set=ON_ERROR_STOP=1 --dbname="$EXPRESSYA_DB" --file=01_modelo_fisico_expressya.sql
BEGIN;

CREATE SCHEMA expressya;
SET LOCAL search_path TO expressya, public;

-- dinero = NUMERIC(14,2) no negativo
CREATE DOMAIN dinero AS NUMERIC(14,2) CHECK (VALUE >= 0);

-- Datos de referencia
CREATE TABLE dark_store (
  darkstore_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  nombre        TEXT NOT NULL CHECK (nombre <> ''),
  ubicacion     TEXT NOT NULL CHECK (ubicacion <> '')
);

CREATE TABLE producto (
  producto_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  nombre        TEXT NOT NULL CHECK (nombre <> ''),
  descripcion   TEXT
);

CREATE TABLE cliente (
  cliente_id    UUID PRIMARY KEY DEFAULT gen_random_uuid()
);

CREATE TABLE usuario_operativo (
  usuario_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  nombre        TEXT NOT NULL CHECK (nombre <> '')
);

CREATE TABLE asignacion_rol (
  usuario_id    UUID NOT NULL REFERENCES usuario_operativo,
  darkstore_id  UUID NOT NULL REFERENCES dark_store,
  rol           TEXT NOT NULL CHECK (rol <> ''),
  PRIMARY KEY (usuario_id, darkstore_id, rol)
);

-- M01 Inventario
CREATE TABLE inventario (
  darkstore_id     UUID NOT NULL REFERENCES dark_store,
  producto_id      UUID NOT NULL REFERENCES producto,
  e                INTEGER NOT NULL DEFAULT 0 CHECK (e >= 0),
  n                INTEGER NOT NULL DEFAULT 0 CHECK (n >= 0),
  estado_producto  TEXT NOT NULL DEFAULT 'HABILITADO'
                   CHECK (estado_producto IN ('HABILITADO','BLOQUEADO')),
  PRIMARY KEY (darkstore_id, producto_id)
);

CREATE TABLE conteo_fisico (
  conteo_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  darkstore_id      UUID NOT NULL,
  producto_id       UUID NOT NULL,
  cantidad_contada  INTEGER NOT NULL CHECK (cantidad_contada >= 0),
  e_al_corte        INTEGER NOT NULL CHECK (e_al_corte >= 0),
  fecha_corte       TIMESTAMPTZ NOT NULL,
  fecha_registro    TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  usuario_id        UUID NOT NULL REFERENCES usuario_operativo,
  FOREIGN KEY (darkstore_id, producto_id) REFERENCES inventario
);

CREATE TABLE movimiento_inventario (
  movimiento_id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  darkstore_id                UUID NOT NULL,
  producto_id                 UUID NOT NULL,
  tipo                        TEXT NOT NULL CHECK (tipo IN (
                                'AJUSTE_INICIAL','RESERVA','RETIRO_RESERVA','EXPIRACION',
                                'LIBERACION','COMPROMISO','SEPARACION','CANCELACION_LINEA',
                                'DISCREPANCIA','RESOLUCION','DANO','REINCORPORACION','DESECHO')),
  fecha                       TIMESTAMPTZ NOT NULL,
  tx_origen                   BIGINT NOT NULL,
  motivo                      TEXT NOT NULL CHECK (motivo <> ''),
  usuario_id                  UUID REFERENCES usuario_operativo,
  autorizado_por              UUID REFERENCES usuario_operativo,
  clave_operacion             UUID NOT NULL,
  referencia_reserva_id       UUID,
  referencia_linea_id         UUID,
  referencia_conteo_id        UUID,
  referencia_inspeccion_id    UUID,
  referencia_resolucion_id    UUID,
  referencia_discrepancia_id  UUID,
  FOREIGN KEY (darkstore_id, producto_id) REFERENCES inventario,
  CHECK (tipo NOT IN ('REINCORPORACION','DESECHO') OR referencia_inspeccion_id IS NOT NULL)
);

-- Detalle de saldos afectados por un movimiento (1 a 0..4: E, N, R, C)
CREATE TABLE movimiento_saldo (
  movimiento_id  UUID NOT NULL REFERENCES movimiento_inventario,
  saldo          CHAR(1) NOT NULL CHECK (saldo IN ('E','N','R','C')),
  cantidad       INTEGER NOT NULL CHECK (cantidad <> 0),
  PRIMARY KEY (movimiento_id, saldo)
);

CREATE TABLE inspeccion_dano (
  inspeccion_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  movimiento_dano_id  UUID NOT NULL REFERENCES movimiento_inventario,
  darkstore_id        UUID NOT NULL,
  producto_id         UUID NOT NULL,
  cantidad            INTEGER NOT NULL CHECK (cantidad > 0),
  resultado           TEXT NOT NULL CHECK (resultado IN ('APTA','NO_APTA')),
  evidencia           TEXT NOT NULL CHECK (evidencia <> ''),
  inspeccionado_por   UUID NOT NULL REFERENCES usuario_operativo,
  fecha               TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  FOREIGN KEY (darkstore_id, producto_id) REFERENCES inventario
);

-- M03 Pedidos y M02 Reservas (referencias recíprocas diferidas)
CREATE TABLE pedido (
  pedido_id            UUID PRIMARY KEY,
  reserva_id           UUID NOT NULL UNIQUE,
  cliente_id           UUID NOT NULL REFERENCES cliente,
  darkstore_id         UUID NOT NULL REFERENCES dark_store,
  fecha_creacion       TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  total_vigente        dinero NOT NULL,
  estado               TEXT NOT NULL DEFAULT 'PENDIENTE_DE_PAGO'
                       CHECK (estado IN ('PENDIENTE_DE_PAGO','CONFIRMADO','SIN_COMPRA','CANCELADO')),
  ajuste_pendiente     BOOLEAN NOT NULL DEFAULT false,
  intento_aplicado_id  UUID,
  UNIQUE (pedido_id, cliente_id, darkstore_id),
  UNIQUE (pedido_id, reserva_id),
  CHECK ((estado IN ('CONFIRMADO','CANCELADO')) = (intento_aplicado_id IS NOT NULL))
);

CREATE TABLE reserva (
  reserva_id     UUID PRIMARY KEY,
  pedido_id      UUID NOT NULL UNIQUE,
  cliente_id     UUID NOT NULL REFERENCES cliente,
  darkstore_id   UUID NOT NULL REFERENCES dark_store,
  t_creacion     TIMESTAMPTZ NOT NULL,
  t_vence        TIMESTAMPTZ NOT NULL,
  estado         TEXT NOT NULL DEFAULT 'ACTIVA'
                 CHECK (estado IN ('ACTIVA','COMPROMETIDA','EXPIRADA','LIBERADA')),
  motivo_cierre  TEXT,
  fecha_cierre   TIMESTAMPTZ,
  UNIQUE (reserva_id, pedido_id),
  UNIQUE (reserva_id, darkstore_id),
  CHECK (t_vence = t_creacion + INTERVAL '300 seconds'),
  CHECK ((estado = 'ACTIVA') = (fecha_cierre IS NULL)),
  CHECK (estado IN ('ACTIVA','COMPROMETIDA') OR motivo_cierre IS NOT NULL)
);

CREATE TABLE linea_reserva (
  reserva_id       UUID NOT NULL,
  producto_id      UUID NOT NULL,
  darkstore_id     UUID NOT NULL,
  cantidad         INTEGER NOT NULL CHECK (cantidad > 0),
  precio_unitario  dinero NOT NULL,
  fecha_retiro     TIMESTAMPTZ,
  discrepancia_id  UUID,
  PRIMARY KEY (reserva_id, producto_id),
  UNIQUE (reserva_id, producto_id, darkstore_id),
  FOREIGN KEY (reserva_id, darkstore_id) REFERENCES reserva (reserva_id, darkstore_id),
  FOREIGN KEY (darkstore_id, producto_id) REFERENCES inventario,
  CHECK ((fecha_retiro IS NULL) = (discrepancia_id IS NULL))
);

CREATE TABLE linea_pedido (
  linea_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  pedido_id         UUID NOT NULL,
  reserva_id        UUID NOT NULL,
  producto_id       UUID NOT NULL,
  darkstore_id      UUID NOT NULL,
  precio_unitario   dinero NOT NULL,
  q                 INTEGER NOT NULL CHECK (q > 0),
  s                 INTEGER NOT NULL DEFAULT 0 CHECK (s >= 0),
  k                 INTEGER NOT NULL DEFAULT 0 CHECK (k >= 0),
  fecha_compromiso  TIMESTAMPTZ NOT NULL,
  estado            TEXT NOT NULL DEFAULT 'ASIGNADA'
                    CHECK (estado IN ('ASIGNADA','NO_SURTIBLE','SEPARADA','PARCIAL','CANCELADA')),
  UNIQUE (reserva_id, producto_id),
  FOREIGN KEY (reserva_id, producto_id, darkstore_id)
    REFERENCES linea_reserva (reserva_id, producto_id, darkstore_id),
  FOREIGN KEY (pedido_id, reserva_id) REFERENCES pedido (pedido_id, reserva_id),
  CHECK (s + k <= q),
  CHECK (CASE estado
           WHEN 'ASIGNADA'    THEN s + k < q
           WHEN 'NO_SURTIBLE' THEN s + k < q
           WHEN 'SEPARADA'    THEN s = q
           WHEN 'CANCELADA'   THEN k = q
           WHEN 'PARCIAL'     THEN s > 0 AND k > 0 AND s + k = q
         END)
);

-- M05 Faltantes
CREATE TABLE discrepancia (
  discrepancia_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  darkstore_id              UUID NOT NULL,
  producto_id               UUID NOT NULL,
  tipo                      TEXT NOT NULL CHECK (tipo <> ''),
  origen                    TEXT NOT NULL CHECK (origen IN ('CONTEO','DANO','REPORTE_PICKER')),
  conteo_origen_id          UUID REFERENCES conteo_fisico,
  movimiento_dano_id        UUID REFERENCES movimiento_inventario,
  reporte_picker_evidencia  TEXT,
  reportado_por             UUID REFERENCES usuario_operativo,
  fecha_deteccion           TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  estado                    TEXT NOT NULL DEFAULT 'EN_VALIDACION'
                            CHECK (estado IN ('EN_VALIDACION','RESUELTA')),
  FOREIGN KEY (darkstore_id, producto_id) REFERENCES inventario,
  CHECK ((origen = 'CONTEO') = (conteo_origen_id IS NOT NULL)),
  CHECK ((origen = 'DANO') = (movimiento_dano_id IS NOT NULL)),
  CHECK (origen <> 'REPORTE_PICKER'
         OR (reporte_picker_evidencia IS NOT NULL AND reportado_por IS NOT NULL))
);

CREATE TABLE resolucion_faltante (
  resolucion_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  discrepancia_id     UUID NOT NULL UNIQUE REFERENCES discrepancia,
  darkstore_id        UUID NOT NULL,
  producto_id         UUID NOT NULL,
  supervisor_id       UUID NOT NULL REFERENCES usuario_operativo,
  unidades_validadas  INTEGER NOT NULL CHECK (unidades_validadas >= 0),
  conteo_id           UUID NOT NULL REFERENCES conteo_fisico,
  evidencia           TEXT NOT NULL CHECK (evidencia <> ''),
  regla_aplicada      TEXT NOT NULL CHECK (regla_aplicada <> ''),
  motivo              TEXT NOT NULL CHECK (motivo <> ''),
  fecha               TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  FOREIGN KEY (darkstore_id, producto_id) REFERENCES inventario
);

CREATE TABLE asignacion_faltante (
  resolucion_id    UUID NOT NULL REFERENCES resolucion_faltante,
  linea_id         UUID NOT NULL REFERENCES linea_pedido,
  darkstore_id     UUID NOT NULL,
  producto_id      UUID NOT NULL,
  separadas_antes  INTEGER NOT NULL CHECK (separadas_antes >= 0),
  asignadas        INTEGER NOT NULL CHECK (asignadas >= 0),
  canceladas       INTEGER NOT NULL CHECK (canceladas >= 0),
  PRIMARY KEY (resolucion_id, linea_id),
  FOREIGN KEY (darkstore_id, producto_id) REFERENCES inventario
);

CREATE TABLE cancelacion_linea (
  cancelacion_id  UUID PRIMARY KEY,
  linea_id        UUID NOT NULL REFERENCES linea_pedido,
  pedido_id       UUID NOT NULL REFERENCES pedido,
  cantidad        INTEGER NOT NULL CHECK (cantidad > 0),
  causa           TEXT NOT NULL CHECK (causa <> ''),
  resolucion_id   UUID NOT NULL,
  usuario_id      UUID NOT NULL REFERENCES usuario_operativo,
  fecha           TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  reembolso_id    UUID NOT NULL UNIQUE,
  FOREIGN KEY (resolucion_id, linea_id) REFERENCES asignacion_faltante (resolucion_id, linea_id)
);

-- M04 Pagos y reembolsos
CREATE TABLE intento_pago (
  intento_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  pedido_id              UUID NOT NULL REFERENCES pedido,
  monto                  dinero NOT NULL CHECK (monto > 0),
  referencia_externa     TEXT,
  fecha_inicio           TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  fecha_resultado        TIMESTAMPTZ,
  estado                 TEXT NOT NULL DEFAULT 'INICIADO' CHECK (estado IN (
                           'INICIADO','EN_VERIFICACION','APROBADO','RECHAZADO',
                           'REEMBOLSO_EN_PROCESO','REEMBOLSO_EN_VERIFICACION','REEMBOLSO_FALLIDO',
                           'REEMBOLSADO_PARCIAL','REEMBOLSADO_TOTAL')),
  evidencia_fuente       TEXT,
  evidencia              JSONB,
  evidencia_responsable  UUID REFERENCES usuario_operativo,
  reembolso_actual_id    UUID,
  UNIQUE (intento_id, pedido_id),
  CHECK ((estado IN ('REEMBOLSO_EN_PROCESO','REEMBOLSO_EN_VERIFICACION','REEMBOLSO_FALLIDO'))
         = (reembolso_actual_id IS NOT NULL)),
  CHECK (evidencia_responsable IS NULL OR evidencia IS NOT NULL)
);

CREATE TABLE reembolso (
  reembolso_id            UUID PRIMARY KEY,
  intento_id              UUID NOT NULL,
  pedido_id               UUID NOT NULL,
  monto                   dinero NOT NULL CHECK (monto > 0),
  motivo                  TEXT NOT NULL CHECK (motivo <> ''),
  cancelacion_id          UUID UNIQUE,
  reserva_origen_id       UUID,
  producto_origen_id      UUID,
  referencia_externa      TEXT,
  fecha_solicitud         TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  fecha_envio             TIMESTAMPTZ,
  fecha_confirmacion      TIMESTAMPTZ,
  fecha_fallo_confirmado  TIMESTAMPTZ,
  evidencia_revision      JSONB,
  responsable_revision    UUID REFERENCES usuario_operativo,
  UNIQUE (reembolso_id, intento_id),
  UNIQUE (reembolso_id, cancelacion_id),
  FOREIGN KEY (intento_id, pedido_id) REFERENCES intento_pago (intento_id, pedido_id),
  FOREIGN KEY (reserva_origen_id, producto_origen_id) REFERENCES linea_reserva (reserva_id, producto_id),
  CHECK ((reserva_origen_id IS NULL) = (producto_origen_id IS NULL)),
  CHECK (cancelacion_id IS NULL OR reserva_origen_id IS NULL),
  CHECK (fecha_confirmacion IS NULL OR fecha_envio IS NOT NULL)
);

CREATE TABLE notificacion_pasarela (
  objeto               TEXT NOT NULL CHECK (objeto IN ('INTENTO','REEMBOLSO')),
  intento_id           UUID REFERENCES intento_pago,
  reembolso_id         UUID REFERENCES reembolso,
  objeto_id            UUID NOT NULL,
  referencia_externa   TEXT NOT NULL,
  resultado_informado  JSONB NOT NULL,
  fecha_recepcion      TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  resultado_aplicado   JSONB NOT NULL,
  PRIMARY KEY (objeto, objeto_id, referencia_externa),
  CHECK ((objeto = 'INTENTO'   AND intento_id   = objeto_id AND reembolso_id IS NULL)
      OR (objeto = 'REEMBOLSO' AND reembolso_id = objeto_id AND intento_id   IS NULL))
);

-- M06 Avisos y M08 Registro
CREATE TABLE aviso (
  aviso_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  destinatario_tipo  TEXT NOT NULL CHECK (destinatario_tipo IN ('CLIENTE','RESPONSABLE')),
  cliente_id         UUID REFERENCES cliente,
  usuario_id         UUID REFERENCES usuario_operativo,
  tipo               TEXT NOT NULL CHECK (tipo <> ''),
  origen_tipo        TEXT NOT NULL CHECK (origen_tipo <> ''),
  origen_id          UUID NOT NULL,
  pedido_id          UUID REFERENCES pedido,
  fecha              TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  clave              UUID NOT NULL UNIQUE,
  entregado_en       TIMESTAMPTZ,
  intentos_entrega   INTEGER NOT NULL DEFAULT 0 CHECK (intentos_entrega >= 0),
  CHECK ((destinatario_tipo = 'CLIENTE') = (cliente_id IS NOT NULL))
);

CREATE TABLE registro_operacion (
  registro_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  clave_operacion    UUID NOT NULL UNIQUE,
  operacion          TEXT NOT NULL CHECK (operacion ~ '^CL-(0[1-9]|1[0-8])$'),
  objeto             TEXT NOT NULL CHECK (objeto <> ''),
  objeto_id          UUID NOT NULL,
  huella_solicitud   BYTEA NOT NULL CHECK (octet_length(huella_solicitud) = 32),
  estado_anterior    JSONB NOT NULL,
  estado_resultante  JSONB NOT NULL,
  actor_tipo         TEXT NOT NULL CHECK (actor_tipo IN ('CLIENTE','USUARIO_OPERATIVO','SISTEMA','PASARELA')),
  usuario_id         UUID REFERENCES usuario_operativo,
  fecha              TIMESTAMPTZ NOT NULL,
  resultado          TEXT NOT NULL CHECK (resultado IN ('APLICADA','RECHAZADA')),
  respuesta          JSONB NOT NULL,
  motivo_rechazo     TEXT,
  CHECK ((resultado = 'RECHAZADA') = (motivo_rechazo IS NOT NULL)),
  CHECK ((actor_tipo = 'USUARIO_OPERATIVO') = (usuario_id IS NOT NULL))
);

CREATE TABLE evento_interno (
  evento_id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tipo                    TEXT NOT NULL CHECK (tipo IN ('EV-01','EV-02','EV-03')),
  productor               TEXT NOT NULL CHECK (productor ~ '^M0[1-8]$'),
  intento_id              UUID REFERENCES intento_pago,
  reserva_id              UUID REFERENCES reserva,
  discrepancia_id         UUID REFERENCES discrepancia,
  objeto_id               UUID,
  fecha                   TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  procesado               BOOLEAN NOT NULL DEFAULT false,
  procesado_en            TIMESTAMPTZ,
  intentos_procesamiento  INTEGER NOT NULL DEFAULT 0 CHECK (intentos_procesamiento >= 0),
  CHECK (procesado = (procesado_en IS NOT NULL)),
  CHECK ((tipo = 'EV-01') = (intento_id IS NOT NULL)),
  CHECK ((tipo = 'EV-02') = (reserva_id IS NOT NULL)),
  CHECK ((tipo = 'EV-03') = (discrepancia_id IS NOT NULL))
);

-- Llaves foráneas que cierran ciclos (comprobadas al COMMIT)
ALTER TABLE pedido ADD CONSTRAINT fk_pedido_reserva
  FOREIGN KEY (reserva_id, pedido_id) REFERENCES reserva (reserva_id, pedido_id)
  DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE reserva ADD CONSTRAINT fk_reserva_pedido
  FOREIGN KEY (pedido_id, cliente_id, darkstore_id) REFERENCES pedido (pedido_id, cliente_id, darkstore_id)
  DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE pedido ADD CONSTRAINT fk_pedido_intento_aplicado
  FOREIGN KEY (intento_aplicado_id, pedido_id) REFERENCES intento_pago (intento_id, pedido_id)
  DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE intento_pago ADD CONSTRAINT fk_intento_reembolso_actual
  FOREIGN KEY (reembolso_actual_id, intento_id) REFERENCES reembolso (reembolso_id, intento_id)
  DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE reembolso ADD CONSTRAINT fk_reembolso_cancelacion
  FOREIGN KEY (cancelacion_id) REFERENCES cancelacion_linea (cancelacion_id)
  DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE cancelacion_linea ADD CONSTRAINT fk_cancelacion_reembolso
  FOREIGN KEY (reembolso_id, cancelacion_id) REFERENCES reembolso (reembolso_id, cancelacion_id)
  DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE linea_reserva ADD CONSTRAINT fk_linea_reserva_discrepancia
  FOREIGN KEY (discrepancia_id) REFERENCES discrepancia;
ALTER TABLE movimiento_inventario
  ADD CONSTRAINT fk_mov_reserva      FOREIGN KEY (referencia_reserva_id)      REFERENCES reserva,
  ADD CONSTRAINT fk_mov_linea        FOREIGN KEY (referencia_linea_id)        REFERENCES linea_pedido,
  ADD CONSTRAINT fk_mov_conteo       FOREIGN KEY (referencia_conteo_id)       REFERENCES conteo_fisico,
  ADD CONSTRAINT fk_mov_inspeccion   FOREIGN KEY (referencia_inspeccion_id)   REFERENCES inspeccion_dano,
  ADD CONSTRAINT fk_mov_resolucion   FOREIGN KEY (referencia_resolucion_id)   REFERENCES resolucion_faltante,
  ADD CONSTRAINT fk_mov_discrepancia FOREIGN KEY (referencia_discrepancia_id) REFERENCES discrepancia;

-- Índices orientados a las operaciones
-- PR-01: propuesta del equipo [DD, Q-18], no requisito del cliente; retirable (ver 5.6)
CREATE UNIQUE INDEX uq_reserva_activa_cliente  ON reserva (cliente_id) WHERE estado = 'ACTIVA';
CREATE INDEX ix_reserva_vencimiento           ON reserva (t_vence) WHERE estado = 'ACTIVA';
CREATE INDEX ix_linea_reserva_saldos          ON linea_reserva (darkstore_id, producto_id) WHERE fecha_retiro IS NULL;
CREATE INDEX ix_linea_pedido_pendiente        ON linea_pedido (darkstore_id, producto_id, fecha_compromiso)
                                              WHERE estado IN ('ASIGNADA','NO_SURTIBLE');
CREATE UNIQUE INDEX uq_intento_abierto_pedido ON intento_pago (pedido_id) WHERE estado IN ('INICIADO','EN_VERIFICACION');
CREATE INDEX ix_intento_verificacion          ON intento_pago (estado, fecha_inicio)
                                              WHERE estado IN ('EN_VERIFICACION','REEMBOLSO_EN_VERIFICACION');
CREATE UNIQUE INDEX uq_reembolso_total_origen ON reembolso (intento_id)
                                              WHERE cancelacion_id IS NULL AND reserva_origen_id IS NULL;
CREATE UNIQUE INDEX uq_reembolso_linea_quitada ON reembolso (reserva_origen_id, producto_origen_id)
                                              WHERE reserva_origen_id IS NOT NULL;
CREATE INDEX ix_movimiento_producto_fecha     ON movimiento_inventario (darkstore_id, producto_id, fecha);
CREATE INDEX ix_registro_objeto_fecha         ON registro_operacion (objeto, objeto_id, fecha);
CREATE UNIQUE INDEX uq_movimiento_inspeccion  ON movimiento_inventario (referencia_inspeccion_id)
                                              WHERE tipo IN ('REINCORPORACION','DESECHO');
CREATE INDEX ix_evento_pendiente              ON evento_interno (fecha) WHERE NOT procesado;
CREATE INDEX ix_aviso_pendiente               ON aviso (fecha) WHERE entregado_en IS NULL;

-- Vistas de saldos (R, C y D derivados; RC-01, RC-02, ADR-08)
-- R se basa en ACTIVA: no se oculta inventario retenido si la limpieza está retrasada.
CREATE VIEW v_saldos_inventario AS
WITH reservados AS (
  SELECT lr.darkstore_id, lr.producto_id, sum(lr.cantidad)::bigint AS r
  FROM expressya.linea_reserva lr JOIN expressya.reserva re USING (reserva_id)
  WHERE re.estado = 'ACTIVA' AND lr.fecha_retiro IS NULL
  GROUP BY lr.darkstore_id, lr.producto_id
), comprometidos AS (
  SELECT darkstore_id, producto_id, sum(q::bigint - s::bigint - k::bigint)::bigint AS c
  FROM expressya.linea_pedido GROUP BY darkstore_id, producto_id
)
SELECT i.darkstore_id, i.producto_id, i.e, i.n, i.estado_producto,
  coalesce(r.r, 0::bigint) AS r, coalesce(c.c, 0::bigint) AS c,
  i.e::bigint - coalesce(r.r, 0::bigint) - coalesce(c.c, 0::bigint) AS d
FROM expressya.inventario i
LEFT JOIN reservados r USING (darkstore_id, producto_id)
LEFT JOIN comprometidos c USING (darkstore_id, producto_id);

CREATE VIEW v_disponibilidad_cliente AS
SELECT darkstore_id, producto_id,
  CASE WHEN estado_producto = 'HABILITADO' AND d >= 1 THEN 'disponible' ELSE 'agotado' END AS disponibilidad
FROM expressya.v_saldos_inventario;

CREATE VIEW v_reconstruccion_saldos AS
SELECT i.darkstore_id, i.producto_id, i.e, i.n,
  coalesce(sum(ms.cantidad) FILTER (WHERE ms.saldo = 'E'), 0)::bigint AS e_historial,
  coalesce(sum(ms.cantidad) FILTER (WHERE ms.saldo = 'N'), 0)::bigint AS n_historial
FROM expressya.inventario i
LEFT JOIN expressya.movimiento_inventario m USING (darkstore_id, producto_id)
LEFT JOIN expressya.movimiento_saldo ms USING (movimiento_id)
GROUP BY i.darkstore_id, i.producto_id, i.e, i.n;

-- Fecha del servidor y transacción de origen
CREATE FUNCTION sellar_fecha_registro() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.fecha := clock_timestamp();
  IF TG_TABLE_NAME = 'movimiento_inventario' THEN
    NEW.tx_origen := txid_current();
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER tr_fecha_movimiento BEFORE INSERT ON movimiento_inventario
  FOR EACH ROW EXECUTE FUNCTION sellar_fecha_registro();
CREATE TRIGGER tr_fecha_registro BEFORE INSERT ON registro_operacion
  FOR EACH ROW EXECUTE FUNCTION sellar_fecha_registro();

-- Historial de solo inserción (UPDATE, DELETE y TRUNCATE rechazados)
CREATE FUNCTION rechazar_cambio_historial() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'El historial de % es de solo insercion', TG_TABLE_NAME USING ERRCODE = '23514';
END $$;
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['movimiento_inventario','movimiento_saldo','conteo_fisico','inspeccion_dano',
                           'resolucion_faltante','asignacion_faltante','cancelacion_linea',
                           'registro_operacion','notificacion_pasarela'] LOOP
    EXECUTE format('CREATE TRIGGER tr_historial_%1$s BEFORE UPDATE OR DELETE ON expressya.%1$I
                    FOR EACH ROW EXECUTE FUNCTION expressya.rechazar_cambio_historial()', t);
    EXECUTE format('CREATE TRIGGER tr_historial_trunc_%1$s BEFORE TRUNCATE ON expressya.%1$I
                    FOR EACH STATEMENT EXECUTE FUNCTION expressya.rechazar_cambio_historial()', t);
  END LOOP;
END $$;

-- El detalle de saldos solo se agrega en la misma transacción que su cabecera
CREATE FUNCTION validar_detalle_movimiento() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM expressya.movimiento_inventario
                 WHERE movimiento_id = NEW.movimiento_id AND tx_origen = txid_current()) THEN
    RAISE EXCEPTION 'El detalle debe registrarse en la transaccion de su movimiento' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER tr_detalle_movimiento BEFORE INSERT ON movimiento_saldo
  FOR EACH ROW EXECUTE FUNCTION validar_detalle_movimiento();

-- Rol asignado en la tienda para quien actúa
CREATE FUNCTION exigir_rol_en_tienda() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE usuarios uuid[]; u uuid;
BEGIN
  IF TG_TABLE_NAME = 'conteo_fisico' THEN
    usuarios := ARRAY[NEW.usuario_id];
  ELSIF TG_TABLE_NAME = 'inspeccion_dano' THEN
    usuarios := ARRAY[NEW.inspeccionado_por];
  ELSIF TG_TABLE_NAME = 'discrepancia' THEN
    usuarios := ARRAY[NEW.reportado_por];
  ELSIF TG_TABLE_NAME = 'resolucion_faltante' THEN
    usuarios := ARRAY[NEW.supervisor_id];
  ELSE
    usuarios := ARRAY[NEW.usuario_id, NEW.autorizado_por];
  END IF;
  FOREACH u IN ARRAY usuarios LOOP
    IF u IS NOT NULL AND NOT EXISTS (SELECT 1 FROM expressya.asignacion_rol
                                     WHERE usuario_id = u AND darkstore_id = NEW.darkstore_id) THEN
      RAISE EXCEPTION 'El usuario % no tiene rol asignado en la tienda', u USING ERRCODE = '23503';
    END IF;
  END LOOP;
  RETURN NEW;
END $$;
CREATE TRIGGER tr_rol_conteo       BEFORE INSERT ON conteo_fisico         FOR EACH ROW EXECUTE FUNCTION exigir_rol_en_tienda();
CREATE TRIGGER tr_rol_inspeccion   BEFORE INSERT ON inspeccion_dano       FOR EACH ROW EXECUTE FUNCTION exigir_rol_en_tienda();
CREATE TRIGGER tr_rol_discrepancia BEFORE INSERT ON discrepancia          FOR EACH ROW EXECUTE FUNCTION exigir_rol_en_tienda();
CREATE TRIGGER tr_rol_resolucion   BEFORE INSERT ON resolucion_faltante   FOR EACH ROW EXECUTE FUNCTION exigir_rol_en_tienda();
CREATE TRIGGER tr_rol_movimiento   BEFORE INSERT ON movimiento_inventario FOR EACH ROW EXECUTE FUNCTION exigir_rol_en_tienda();

-- TTL de 300 s inmutable, nacimiento ACTIVA y salida terminal con el reloj actual
CREATE FUNCTION controlar_reserva() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE ahora timestamptz := clock_timestamp();  -- hora real tras el bloqueo; NOW() conserva el inicio de la transacción
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.estado <> 'ACTIVA' THEN
      RAISE EXCEPTION 'Una reserva nace ACTIVA' USING ERRCODE = '23514';
    END IF;
    NEW.t_creacion := ahora;
    NEW.t_vence    := ahora + INTERVAL '300 seconds';
    RETURN NEW;
  END IF;
  IF OLD.estado <> 'ACTIVA' THEN
    RAISE EXCEPTION 'Una reserva cerrada no se modifica ni se reactiva' USING ERRCODE = '23514';
  END IF;
  IF NEW.t_creacion IS DISTINCT FROM OLD.t_creacion OR NEW.t_vence IS DISTINCT FROM OLD.t_vence
     OR NEW.pedido_id <> OLD.pedido_id OR NEW.cliente_id <> OLD.cliente_id
     OR NEW.darkstore_id <> OLD.darkstore_id THEN
    RAISE EXCEPTION 'El plazo y la identidad de la reserva son inmutables' USING ERRCODE = '23514';
  END IF;
  IF NEW.estado = 'COMPROMETIDA' AND ahora >= OLD.t_vence THEN
    RAISE EXCEPTION 'Reserva vencida: no se puede comprometer' USING ERRCODE = '23514';
  END IF;
  IF NEW.estado = 'EXPIRADA' AND ahora < OLD.t_vence THEN
    RAISE EXCEPTION 'La reserva aun no vence' USING ERRCODE = '23514';
  END IF;
  IF NEW.estado <> 'ACTIVA' THEN
    NEW.fecha_cierre := ahora;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER tr_reserva_tiempo BEFORE INSERT OR UPDATE ON reserva
  FOR EACH ROW EXECUTE FUNCTION controlar_reserva();

-- La inspección refiere un movimiento de daño de la misma tienda y producto
CREATE FUNCTION validar_inspeccion() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM expressya.movimiento_inventario
                 WHERE movimiento_id = NEW.movimiento_dano_id AND tipo = 'DANO'
                   AND darkstore_id = NEW.darkstore_id AND producto_id = NEW.producto_id) THEN
    RAISE EXCEPTION 'La inspeccion debe referir un movimiento de dano' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER tr_inspeccion_origen BEFORE INSERT ON inspeccion_dano
  FOR EACH ROW EXECUTE FUNCTION validar_inspeccion();

-- Reincorporación: inspección APTA y dos personas distintas (D-09); desecho: NO_APTA
CREATE FUNCTION validar_reincorporacion() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE inspeccion expressya.inspeccion_dano%ROWTYPE;
BEGIN
  IF NEW.tipo IN ('REINCORPORACION','DESECHO') THEN
    SELECT * INTO inspeccion FROM expressya.inspeccion_dano
      WHERE inspeccion_id = NEW.referencia_inspeccion_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'Falta inspeccion' USING ERRCODE = '23503';
    END IF;
    IF NEW.tipo = 'REINCORPORACION' AND (inspeccion.resultado <> 'APTA'
       OR NEW.autorizado_por IS NULL OR NEW.autorizado_por = inspeccion.inspeccionado_por) THEN
      RAISE EXCEPTION 'Reincorporacion exige APTA y dos personas distintas' USING ERRCODE = '23514';
    END IF;
    IF NEW.tipo = 'DESECHO' AND inspeccion.resultado <> 'NO_APTA' THEN
      RAISE EXCEPTION 'Desecho exige inspeccion NO_APTA' USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER tr_reincorporacion BEFORE INSERT ON movimiento_inventario
  FOR EACH ROW EXECUTE FUNCTION validar_reincorporacion();

-- RC-01 reforzado: un producto HABILITADO nunca queda con D < 0 al COMMIT
CREATE FUNCTION verificar_saldo_habilitado() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v record;
BEGIN
  SELECT estado_producto, d INTO v FROM expressya.v_saldos_inventario
   WHERE darkstore_id = NEW.darkstore_id AND producto_id = NEW.producto_id;
  IF v.estado_producto = 'HABILITADO' AND v.d < 0 THEN
    RAISE EXCEPTION 'Saldo D negativo en un producto HABILITADO (%/%)', NEW.darkstore_id, NEW.producto_id
      USING ERRCODE = '23514';
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER tr_saldo_inventario AFTER UPDATE ON inventario
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION verificar_saldo_habilitado();
CREATE CONSTRAINT TRIGGER tr_saldo_linea_reserva AFTER INSERT OR UPDATE ON linea_reserva
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION verificar_saldo_habilitado();
CREATE CONSTRAINT TRIGGER tr_saldo_linea_pedido AFTER INSERT OR UPDATE ON linea_pedido
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION verificar_saldo_habilitado();

-- INV-09: lo reembolsado nunca supera lo cobrado
CREATE FUNCTION verificar_tope_reembolso() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE cobrado numeric; pedido_total numeric;
BEGIN
  SELECT monto INTO cobrado FROM expressya.intento_pago WHERE intento_id = NEW.intento_id;
  SELECT coalesce(sum(monto), 0) INTO pedido_total FROM expressya.reembolso WHERE intento_id = NEW.intento_id;
  IF pedido_total > cobrado THEN
    RAISE EXCEPTION 'Lo reembolsado (%) supera lo cobrado (%)', pedido_total, cobrado USING ERRCODE = '23514';
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER tr_tope_reembolso AFTER INSERT OR UPDATE OF monto ON reembolso
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION verificar_tope_reembolso();

-- RC-24: un pedido solo pasa a CANCELADO cuando todas sus líneas están canceladas
CREATE FUNCTION validar_pedido_cancelado() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.estado = 'CANCELADO' AND EXISTS (SELECT 1 FROM expressya.linea_pedido
                                          WHERE pedido_id = NEW.pedido_id AND estado <> 'CANCELADA') THEN
    RAISE EXCEPTION 'El pedido conserva lineas no canceladas' USING ERRCODE = '23514';
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER tr_pedido_cancelado AFTER UPDATE OF estado ON pedido
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validar_pedido_cancelado();

-- Comentarios
COMMENT ON TABLE movimiento_saldo IS 'Detalle fisico del saldo y cantidad de MovimientoInventario. Mismo modulo M01.';
COMMENT ON COLUMN intento_pago.reembolso_actual_id IS 'Referencia tecnica al reembolso que ocupa la maquina del intento; no crea maquina paralela.';
COMMENT ON COLUMN reembolso.fecha_envio IS 'NULL mientras el reembolso espera a que termine el anterior del mismo cobro (uno a la vez, ADR-04).';
COMMENT ON COLUMN reembolso.fecha_fallo_confirmado IS 'Evidencia temporal del ultimo fallo confirmado; un reintento posterior se registra en RegistroOperacion.';
COMMENT ON COLUMN registro_operacion.huella_solicitud IS 'SHA-256 de la solicitud canonica sin secretos; la calcula la capa de servicio.';
COMMENT ON COLUMN linea_pedido.fecha_compromiso IS 'Reloj del servidor en CL-07 al confirmar el pago aprobado; criterio FIFO de D-05 (PV-01).';
COMMENT ON VIEW v_saldos_inventario IS 'RC-02 y ADR-08: R, C y D derivados. D >= 0 en productos HABILITADO lo garantiza el protocolo de AT-1 a AT-7 y lo comprueban los constraint triggers tr_saldo_*.';

-- Permisos: nada para PUBLIC; el rol de ejecución recibe solo lo necesario
-- (GRANT por procedimiento administrativo separado; credenciales en Vault)
REVOKE ALL ON SCHEMA expressya FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA expressya FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA expressya FROM PUBLIC;

COMMIT;
