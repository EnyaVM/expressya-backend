BEGIN;
SET LOCAL search_path TO expressya, public;

INSERT INTO producto (producto_id, nombre, precio_unitario)
VALUES ('55555555-5555-5555-5555-555555555551', 'Sudadera gris con capucha L', 1200.00),
       ('55555555-5555-5555-5555-555555555552', 'Calcetas deportivas 3 pares', 300.00),
       ('55555555-5555-5555-5555-555555555553', 'Chamarra ligera negra M', 1000.00)
ON CONFLICT (producto_id) DO UPDATE
SET nombre = EXCLUDED.nombre, precio_unitario = EXCLUDED.precio_unitario;

COMMIT;
