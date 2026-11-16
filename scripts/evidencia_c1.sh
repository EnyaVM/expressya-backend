#!/usr/bin/env bash
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python}"
REPETICIONES="${REPETICIONES:-20}"
mkdir -p evidencia
SALIDA=evidencia/c1_resultados.txt
FALLAS=0

{
  echo "Evidencia C1 - ExpressYa (Equipo 03)"
  echo "Fecha: $(date '+%Y-%m-%d %H:%M:%S %z')"
  echo "Python: $($PYTHON --version 2>&1)"
  echo

  echo "== 1. CL-02: reserva con vencimiento a 300 s y carrera por la ultima unidad =="
  $PYTHON -m pytest tests/test_checkout.py -v -W ignore || FALLAS=$((FALLAS + 1))
  echo

  echo "== 2. CL-03, expiracion, comprobacion al usar y reconstruccion de saldos =="
  $PYTHON -m pytest tests/test_reservas_cierre.py -v -W ignore || FALLAS=$((FALLAS + 1))
  echo

  echo "== 3. Las pruebas de carrera repetidas $REPETICIONES veces =="
  for i in $(seq 1 "$REPETICIONES"); do
    RESULTADO=$($PYTHON -m pytest -q -W ignore -k "ultima_unidad or liberar_y_expirar" 2>&1 | tail -1)
    echo "corrida $i: $RESULTADO"
    case "$RESULTADO" in *failed*|*error*) FALLAS=$((FALLAS + 1)) ;; esac
  done
  echo

  if [ "$FALLAS" -eq 0 ]; then
    echo "RESULTADO FINAL: todo paso"
  else
    echo "RESULTADO FINAL: hubo $FALLAS bloques con fallas"
  fi
} 2>&1 | tee "$SALIDA"

echo
echo "Log guardado en $SALIDA"