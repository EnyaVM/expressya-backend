#!/bin/bash
set -e
DEMO_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$DEMO_ROOT"
if [ ! -x .venv/bin/python ]; then
  if ! command -v python3.12 >/dev/null; then
    echo 'Se necesita Python 3.12. Instálalo y vuelve a abrir este archivo.'
    exit 1
  fi
  python3.12 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
fi
export DATABASE_URL="${DATABASE_URL:-postgresql://postgres:postgres@localhost:5432/expressya_demo_20261123}"
export EXPRESSYA_DEMO=1
if command -v docker >/dev/null && docker container inspect expressya-db >/dev/null 2>&1; then
  docker start expressya-db >/dev/null
fi
.venv/bin/python scripts/preparar_demo.py
.venv/bin/python -m scripts.crear_cuentas_demo
if curl -fsS http://127.0.0.1:8001/v1/salud >/dev/null 2>&1; then
  echo 'ExpressYa ya está abierto en http://127.0.0.1:8001'
  exit 0
fi
echo 'Abre http://127.0.0.1:8001 en tu navegador. Deja esta terminal abierta durante la demo.'
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
