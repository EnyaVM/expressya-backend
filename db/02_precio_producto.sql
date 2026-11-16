BEGIN;
SET LOCAL search_path TO expressya, public;

ALTER TABLE producto ADD COLUMN precio_unitario dinero NOT NULL CHECK (precio_unitario > 0);

COMMIT;