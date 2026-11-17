BEGIN;
SET LOCAL search_path TO expressya, public;

INSERT INTO usuario_operativo (usuario_id, nombre)
VALUES ('77777777-7777-7777-7777-777777777777', 'Responsable de prueba');

INSERT INTO asignacion_rol (usuario_id, darkstore_id, rol)
VALUES ('77777777-7777-7777-7777-777777777777', '11111111-1111-1111-1111-111111111111', 'RESPONSABLE');

COMMIT;
