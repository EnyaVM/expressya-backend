BEGIN;
SET LOCAL search_path TO expressya, public;

INSERT INTO dark_store (darkstore_id, nombre, ubicacion)
VALUES ('11111111-1111-1111-1111-111111111112', 'Dark Store Sur', 'Sur'),
       ('11111111-1111-1111-1111-111111111113', 'Dark Store Centro', 'Centro'),
       ('11111111-1111-1111-1111-111111111114', 'Dark Store Oriente', 'Oriente'),
       ('11111111-1111-1111-1111-111111111115', 'Dark Store Poniente', 'Poniente');

INSERT INTO producto (producto_id, nombre)
VALUES ('55555555-5555-5555-5555-555555555551', 'Sudadera gris con capucha L'),
       ('55555555-5555-5555-5555-555555555552', 'Calcetas deportivas 3 pares'),
       ('55555555-5555-5555-5555-555555555553', 'Chamarra ligera negra M');

INSERT INTO asignacion_rol (usuario_id, darkstore_id, rol)
SELECT '44444444-4444-4444-4444-444444444444', darkstore_id, 'PICKER'
FROM dark_store
ON CONFLICT DO NOTHING;

INSERT INTO inventario (darkstore_id, producto_id)
SELECT d.darkstore_id, p.producto_id
FROM dark_store d CROSS JOIN producto p
ON CONFLICT DO NOTHING;

WITH m AS (
  INSERT INTO movimiento_inventario (darkstore_id, producto_id, tipo, motivo, usuario_id, clave_operacion)
  SELECT darkstore_id, producto_id, 'AJUSTE_INICIAL', 'carga inicial C1',
         '44444444-4444-4444-4444-444444444444', gen_random_uuid()
  FROM inventario
  WHERE e = 0 AND NOT (darkstore_id = '11111111-1111-1111-1111-111111111111'
                      AND producto_id = '33333333-3333-3333-3333-333333333333')
  RETURNING movimiento_id
)
INSERT INTO movimiento_saldo (movimiento_id, saldo, cantidad)
SELECT movimiento_id, 'E', 10 FROM m;

UPDATE inventario SET e = 10
WHERE e = 0 AND NOT (darkstore_id = '11111111-1111-1111-1111-111111111111'
                      AND producto_id = '33333333-3333-3333-3333-333333333333');

COMMIT;

