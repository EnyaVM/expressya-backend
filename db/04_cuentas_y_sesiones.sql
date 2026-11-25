BEGIN;
SET LOCAL search_path TO expressya, public;

-- PROPUESTA del equipo (construccion): credenciales para el portal. Cada cuenta es de un solo tipo;
-- la de cliente apunta a su cliente y la interna a su usuario operativo (sus perfiles siguen en
-- asignacion_rol). La de administracion no tiene identidad operativa: consulta, da de alta cuentas
-- y cambia accesos, pero no opera en nombre de nadie.
CREATE TABLE cuenta (
  cuenta_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  usuario     TEXT NOT NULL UNIQUE CHECK (usuario ~ '^[a-z0-9._-]{3,40}$'),
  nombre      TEXT NOT NULL CHECK (nombre <> ''),
  clave_hash  TEXT NOT NULL CHECK (clave_hash LIKE 'scrypt$%'),
  tipo        TEXT NOT NULL CHECK (tipo IN ('CLIENTE','INTERNO','ADMINISTRADOR')),
  cliente_id  UUID UNIQUE REFERENCES cliente,
  usuario_id  UUID UNIQUE REFERENCES usuario_operativo,
  activo      BOOLEAN NOT NULL DEFAULT true,
  creado_en   TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  CHECK ((tipo = 'CLIENTE') = (cliente_id IS NOT NULL)),
  CHECK ((tipo = 'INTERNO') = (usuario_id IS NOT NULL))
);

-- Se guarda solo el SHA-256 del token: si alguien lee la tabla no puede usar las sesiones.
CREATE TABLE sesion (
  token_hash  BYTEA PRIMARY KEY CHECK (octet_length(token_hash) = 32),
  cuenta_id   UUID NOT NULL REFERENCES cuenta,
  portal      TEXT NOT NULL CHECK (portal IN ('cliente','interno')),
  csrf        TEXT NOT NULL CHECK (length(csrf) >= 32),
  creada_en   TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  expira_en   TIMESTAMPTZ NOT NULL,
  cerrada_en  TIMESTAMPTZ,
  CHECK (expira_en > creada_en)
);
CREATE INDEX ix_sesion_cuenta_abierta ON sesion (cuenta_id) WHERE cerrada_en IS NULL;

CREATE TABLE auditoria_cuenta (
  auditoria_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  accion           TEXT NOT NULL CHECK (accion IN ('ALTA_CUENTA','CAMBIO_ACCESO')),
  cuenta_id        UUID NOT NULL REFERENCES cuenta,
  actor_cuenta_id  UUID REFERENCES cuenta,
  detalle          JSONB NOT NULL,
  fecha            TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER tr_historial_auditoria_cuenta BEFORE UPDATE OR DELETE ON auditoria_cuenta
  FOR EACH ROW EXECUTE FUNCTION rechazar_cambio_historial();

COMMIT;
