"""Orquesta la carga completa de infraestructura ejecutando los loaders del proyecto.

Orden de ejecución:
1) VPC
2) IAM
3) S3
4) EC2 (app)
5) DB (PostgreSQL)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent

# Orden lógico de dependencias:
# - VPC e IAM son base para EC2/DB.
# - S3 se crea antes de cómputo para que los buckets ya existan.
LOAD_ORDER = [
    "load_VPC.py",
    "load_IAM.py",
    "load_S3.py",
    "load_EC2.py",
    "load_DB.py",
]


def run_script(script_name: str) -> None:
    """Ejecuta un script loader y falla inmediatamente si devuelve error."""
    script_path = SCRIPTS_DIR / script_name
    if not script_path.exists():
        raise FileNotFoundError(f"No se encontró el script: {script_path}")

    print(f"\n=== Ejecutando {script_name} ===")
    subprocess.run([sys.executable, str(script_path)], check=True)
    print(f"=== OK: {script_name} ===")


def main() -> int:
    """Lanza todos los loaders en el orden definido."""
    print("Iniciando carga completa de infraestructura...")
    print("Orden:")
    for index, script_name in enumerate(LOAD_ORDER, start=1):
        print(f"  {index}. {script_name}")

    try:
        for script_name in LOAD_ORDER:
            run_script(script_name)
    except subprocess.CalledProcessError as exc:
        print(f"\nFallo en '{Path(exc.args[0][-1]).name}' con código de salida {exc.returncode}.")
        return exc.returncode
    except Exception as exc:
        print(f"\nError no controlado: {exc}")
        return 1

    print("\nCarga completa finalizada con éxito.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
