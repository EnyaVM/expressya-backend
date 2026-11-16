BEGIN;
SET LOCAL search_path TO expressya, public;

INSERT INTO dark_store (darkstore_id, nombre, ubicacion)
VALUES ('11111111-1111-1111-1111-111111111111', 'Dark Store Norte', 'Norte');

INSERT INTO producto (producto_id, nombre)
VALUES ('22222222-2222-2222-2222-222222222222', 'Playera básica negra M'),
       ('33333333-3333-3333-3333-333333333333', 'Jeans slim azul 32');

INSERT INTO usuario_operativo (usuario_id, nombre)
VALUES ('44444444-4444-4444-4444-444444444444', 'Picker de prueba');

INSERT INTO asignacion_rol
VALUES ('44444444-4444-4444-4444-444444444444', '11111111-1111-1111-1111-111111111111', 'PICKER');

INSERT INTO inventario (darkstore_id, producto_id)
VALUES ('11111111-1111-1111-1111-111111111111', '22222222-2222-2222-2222-222222222222'),
       ('11111111-1111-1111-1111-111111111111', '33333333-3333-3333-3333-333333333333');

WITH m AS (
  INSERT INTO movimiento_inventario (darkstore_id, producto_id, tipo, motivo, usuario_id, clave_operacion)
  VALUES ('11111111-1111-1111-1111-111111111111', '22222222-2222-2222-2222-222222222222',
          'AJUSTE_INICIAL', 'carga inicial', '44444444-4444-4444-4444-444444444444', gen_random_uuid())
  RETURNING movimiento_id
)
INSERT INTO movimiento_saldo (movimiento_id, saldo, cantidad)
SELECT movimiento_id, 'E', 3 FROM m;

UPDATE inventario SET e = e + 3
WHERE darkstore_id = '11111111-1111-1111-1111-111111111111'
  AND producto_id = '22222222-2222-2222-2222-222222222222';

COMMIT;
