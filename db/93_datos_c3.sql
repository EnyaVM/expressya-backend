BEGIN;
SET LOCAL search_path TO expressya, public;

INSERT INTO usuario_operativo (usuario_id, nombre)
VALUES ('88888888-8888-8888-8888-888888888888', 'Supervisor de prueba'),
       ('99999999-9999-9999-9999-999999999999', 'Personal de tienda de prueba');

INSERT INTO asignacion_rol (usuario_id, darkstore_id, rol)
SELECT '88888888-8888-8888-8888-888888888888'::uuid, darkstore_id, 'SUPERVISOR' FROM dark_store
UNION ALL
SELECT '99999999-9999-9999-9999-999999999999'::uuid, darkstore_id, 'PERSONAL_TIENDA' FROM dark_store;

COMMIT;
