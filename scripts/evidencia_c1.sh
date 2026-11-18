#!/usr/bin/env bash
set -eu -o pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python}"
REPETICIONES="${REPETICIONES:-20}"
mkdir -p evidencia
SALIDA=evidencia/c1_resultados.txt

{
  echo "Evidencia C1 - ExpressYa (Equipo 03)"
  echo "Fecha: $(date '+%Y-%m-%d %H:%M:%S %z')"
  "$PYTHON" --version
  echo
  echo "== Suite completa =="
  "$PYTHON" -m pytest -v || exit 1

  echo
  echo "== Pruebas de concurrencia =="
  for ((i=1; i<=REPETICIONES; i++)); do
    echo "Corrida $i de $REPETICIONES"
    "$PYTHON" -m pytest -q --disable-warnings -k 'carrera or ultima_unidad or liberar_y_expirar' || exit 1
  done
  echo "RESULTADO FINAL: todo paso"
} 2>&1 | tee "$SALIDA"

echo "Log guardado en $SALIDA"
