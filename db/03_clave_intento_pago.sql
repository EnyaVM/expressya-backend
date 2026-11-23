BEGIN;
SET LOCAL search_path TO expressya, public;

-- PROPUESTA del equipo (construccion): la clave del cobro queda guardada en el intento
-- antes de llamar a la pasarela, para que una misma clave no produzca dos cobros
-- aunque el proceso se caiga entre el cobro y el registro de la respuesta.
ALTER TABLE intento_pago
  ADD COLUMN clave_operacion  UUID UNIQUE,
  ADD COLUMN huella_solicitud BYTEA CHECK (huella_solicitud IS NULL OR octet_length(huella_solicitud) = 32),
  ADD CHECK ((clave_operacion IS NULL) = (huella_solicitud IS NULL));

COMMIT;
