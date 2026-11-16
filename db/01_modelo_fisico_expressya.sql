-- ExpressYa · Equipo 03 · Semana 3 · Modelo físico (PostgreSQL 16)
-- 01_modelo_fisico_expressya.sql — esquema expressya
-- Ejecutar una sola vez en una base de datos vacía

BEGIN;

CREATE SCHEMA IF NOT EXISTS expressya;
SET LOCAL search_path TO expressya, public;

-- Dominio monetario no negativo
CREATE DOMAIN dinero AS NUMERIC(14,2) CHECK (VALUE >= 0);

-- 1. Datos de referencia y catálogo
CREATE TABLE expressya.dark_store (
    darkstore_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    nombre TEXT NOT NULL CHECK (nombre <> ''),
    ubicacion TEXT NOT NULL CHECK (ubicacion <> '')
);

CREATE TABLE expressya.producto (
    producto_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    nombre TEXT NOT NULL CHECK (nombre <> ''),
    descripcion TEXT
);

CREATE TABLE expressya.cliente (
    cliente_id UUID PRIMARY KEY DEFAULT gen_random_uuid()
);

-- 2. Inventario y existencias por Dark Store
CREATE TABLE expressya.inventario_dark_store (
    darkstore_id UUID NOT NULL REFERENCES expressya.dark_store(darkstore_id),
    producto_id UUID NOT NULL REFERENCES expressya.producto(producto_id),
    existencia_vendible INT NOT NULL CHECK (existencia_vendible >= 0),
    existencia_no_vendible INT NOT NULL DEFAULT 0 CHECK (existencia_no_vendible >= 0),
    comprometido INT NOT NULL DEFAULT 0 CHECK (comprometido >= 0),
    reservado INT NOT NULL DEFAULT 0 CHECK (reservado >= 0),
    estado_sku VARCHAR(32) NOT NULL DEFAULT 'HABILITADO',
    PRIMARY KEY (darkstore_id, producto_id)
);

-- 3. Vista de Saldos de Inventario para cálculo en tiempo real ($D = E - R - C$)
CREATE OR REPLACE VIEW expressya.v_saldos_inventario AS
SELECT 
    darkstore_id,
    producto_id,
    existencia_vendible AS e,
    existencia_no_vendible AS n,
    reservado AS r,
    comprometido AS c,
    (existencia_vendible - reservado - comprometido) AS d,
    estado_sku
FROM expressya.inventario_dark_store;

COMMIT;
