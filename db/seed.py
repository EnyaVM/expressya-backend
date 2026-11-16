import os
import psycopg
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/expressya_db")

def seed_database():
    print(" Iniciando carga de datos semilla para ExpressYa...")
    
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            # 1. Insertar las 5 Dark Stores
            dark_stores = [
                (1, "Dark Store Norte", "Zona Norte - Cobertura A"),
                (2, "Dark Store Sur", "Zona Sur - Cobertura B"),
                (3, "Dark Store Centro", "Zona Centro - Cobertura C"),
                (4, "Dark Store Oriente", "Zona Oriente - Cobertura D"),
                (5, "Dark Store Poniente", "Zona Poniente - Cobertura E"),
            ]
            
            cur.executemany("""
                INSERT INTO expressya.dark_store (darkstore_id, nombre, ubicacion)
                VALUES (%s, %s, %s)
                ON CONFLICT (darkstore_id) DO NOTHING;
            """, dark_stores)
            
            # 2. Productos de prueba (incluyendo la última unidad para concurrencia)
            productos = [
                (101, "Café Express 500g", "Café de grano premium"),
                (102, "Leche Entera 1L", "Lácteos frescos"),
                (103, "Pan de Caja Integral", "Panadería"),
                (104, "Smartphone Último Modelo", "Electrónica - Stock de prueba (1 unidad)"),
            ]
            
            cur.executemany("""
                INSERT INTO expressya.producto (producto_id, nombre, descripcion)
                VALUES (%s, %s, %s)
                ON CONFLICT (producto_id) DO NOTHING;
            """, productos)
            
            # 3. Inventario inicial en Dark Store 1
            inventarios = [
                (1, 101, 50, 0),
                (1, 102, 30, 0),
                (1, 103, 20, 0),
                (1, 104, 1, 0),   # ¡Existencia = 1 para la prueba de concurrencia!
            ]
            
            cur.executemany("""
                INSERT INTO expressya.inventario (darkstore_id, producto_id, e, n, estado_producto)
                VALUES (%s, %s, %s, %s, 'HABILITADO')
                ON CONFLICT (darkstore_id, producto_id) DO UPDATE 
                SET e = EXCLUDED.e;
            """, inventarios)
            
            conn.commit()
            print(" Datos semilla cargados exitosamente!")

if __name__ == "__main__":
    seed_database()
