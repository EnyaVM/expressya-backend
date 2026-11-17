import subprocess
import sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SALIDA = RAIZ / "evidencia" / "c2_resultados.txt"
REPETICIONES = int(sys.argv[1]) if len(sys.argv) > 1 else 20
CARRERAS = "carrera or ultima_unidad or liberar_y_expirar"


def correr(argumentos):
    resultado = subprocess.run(
        [sys.executable, "-m", "pytest", *argumentos],
        cwd=RAIZ, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return resultado.returncode, resultado.stdout + resultado.stderr


def main():
    SALIDA.parent.mkdir(exist_ok=True)
    fallas = 0
    texto = [
        "Evidencia C2 - ExpressYa (Equipo 03)",
        f"Fecha: {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %z')}",
        f"Python: {sys.version.split()[0]}",
        "",
        "== 1. Todas las pruebas (C1 y C2) ==",
    ]

    codigo, salida = correr(["-v", "-W", "ignore"])
    texto.append(salida)
    fallas += codigo != 0

    texto.append(f"== 2. Pruebas de carrera repetidas {REPETICIONES} veces ==")
    for numero in range(1, REPETICIONES + 1):
        codigo, salida = correr(["-q", "-W", "ignore", "-k", CARRERAS])
        ultima = salida.strip().splitlines()[-1] if salida.strip() else "sin salida"
        texto.append(f"corrida {numero}: {ultima}")
        fallas += codigo != 0

    texto.append("")
    texto.append("RESULTADO FINAL: todo paso" if fallas == 0 else f"RESULTADO FINAL: hubo {fallas} bloques con fallas")

    contenido = "\n".join(texto)
    SALIDA.write_text(contenido, encoding="utf-8")
    print(contenido)
    print(f"\nLog guardado en {SALIDA.relative_to(RAIZ)}")
    return 1 if fallas else 0


if __name__ == "__main__":
    sys.exit(main())