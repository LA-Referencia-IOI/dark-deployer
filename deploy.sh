#!/usr/bin/env bash
#
# deploy.sh -- lanzador del deployer declarativo v3.
#
# Ejecuta deploy.py con el intérprete del venv del repositorio y le pasa los
# argumentos tal cual. Conserva el directorio desde el que se invoca, para que
# las rutas relativas (--inventory, --output, ...) resuelvan exactamente como se
# escribieron. Sin argumentos muestra la ayuda del CLI.
#
# Si el venv no existe, lo crea e instala requirements.txt y requirements-tui.txt
# (esta última aporta la TUI de Textual). En cada arranque después comprueba dos
# cosas, en este orden:
#
#   - Sello: venv/.deploy-requirements.stamp guarda la huella de los dos ficheros
#     de requisitos y del intérprete. Si no cuadra (venv recién creado, o los
#     requisitos han cambiado), reaplica los ficheros completos: es la única
#     forma de que el entorno sea el que declaran, y sus pins mandan.
#   - Dependencias: con el sello al día, que los módulos que el deployer importa
#     sean importables. Si falta alguno instala SÓLO las líneas afectadas, no el
#     fichero entero: reaplicar los pins de 2023-2024 bajaría de versión todo lo
#     que otras herramientas de ese mismo venv (jupyter, ipython, fastapi...)
#     hayan subido. Esta reparación no toca el sello.
#
# Qué es fatal y qué no: con el sello roto, un fallo de instalación detiene el
# script (el entorno no es el que declaran los ficheros). En una reparación, un
# fallo (por ejemplo, sin red) sólo avisa: la orden pedida puede no necesitar ese
# módulo, y el propio deploy.py ya explica qué instalar cuando le falta Textual.
#
# Uso:
#   ./deploy.sh status --inventory inventory.json
#   ./deploy.sh metrics
#   DARK_PYTHON=/opt/homebrew/bin/python3.12 ./deploy.sh validate --inventory inventory.json
#
# Compatible con el bash 3.2 de macOS: ni arrays asociativos ni otras
# construcciones de bash 4+.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
venv="$here/venv"
py="$venv/bin/python"
requirements="$here/requirements.txt"
requirements_tui="$here/requirements-tui.txt"
stamp="$venv/.deploy-requirements.stamp"

if [ ! -f "$requirements" ] || [ ! -f "$requirements_tui" ]; then
  echo "[deploy] faltan $requirements o $requirements_tui; ¿está el script fuera del repositorio?" >&2
  exit 1
fi

# Elige el intérprete con el que crear el venv. requirements.txt fija versiones
# de 2023-2024 (web3 6.20.0, ckzg 1.0.2, cytoolz 0.12.3), así que 3.12 es la
# apuesta segura y 3.13 queda al final. Un DARK_PYTHON explícito también debe
# cumplir el mínimo: nunca se crea el entorno con Python menor que 3.10.
is_supported_python() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

choose_python() {
  if [ -n "${DARK_PYTHON:-}" ]; then
    if ! command -v "$DARK_PYTHON" >/dev/null 2>&1 && [ ! -x "$DARK_PYTHON" ]; then
      echo "[deploy] DARK_PYTHON no apunta a un intérprete ejecutable: $DARK_PYTHON" >&2
      return 1
    fi
    if ! is_supported_python "$DARK_PYTHON"; then
      echo "[deploy] DARK_PYTHON debe ser Python 3.10 o superior: $DARK_PYTHON" >&2
      return 1
    fi
    printf '%s\n' "$DARK_PYTHON"
    return
  fi
  local candidate resolved
  for candidate in python3.12 python3.11 python3.10 python3.13 python3; do
    if [ -x "$candidate" ]; then
      resolved="$candidate"
    elif command -v "$candidate" >/dev/null 2>&1; then
      resolved="$(command -v "$candidate")"
    else
      continue
    fi
    if is_supported_python "$resolved"; then
      printf '%s\n' "$resolved"
      return
    fi
  done
}

if [ -d "$venv" ] && [ ! -x "$py" ]; then
  echo "[deploy] $venv existe pero $py no es un intérprete utilizable (¿symlink roto?)." >&2
  echo "[deploy] Borra venv/ y vuelve a lanzar ./deploy.sh, o reinstala el Python que le falta." >&2
  exit 1
fi

if [ -x "$py" ] && ! is_supported_python "$py"; then
  echo "[deploy] $py usa Python menor que 3.10; no se puede usar este venv." >&2
  echo "[deploy] Elimina venv/ y vuelve a lanzar ./deploy.sh con un Python compatible." >&2
  exit 1
fi

fresh=0
if [ ! -x "$py" ]; then
  base="$(choose_python || true)"
  if [ -z "$base" ]; then
  echo "[deploy] no hay ningún Python 3.10 o superior; define DARK_PYTHON con la ruta a un intérprete compatible" >&2
    exit 1
  fi
  echo "[deploy] creando venv en $venv con $base"
  if ! "$base" -m venv "$venv"; then
    echo "[deploy] no se pudo crear el venv" >&2
    exit 1
  fi
  fresh=1
fi

# Comprueba el entorno sin ejecutar los módulos (find_spec no importa nada).
# Salidas: 0 todo en orden; 8 el sello no cuadra con los ficheros de requisitos o
# con el intérprete (y entonces manda reaplicar el fichero entero); 9 el sello
# está al día pero falta alguna dependencia declarada, y las líneas de requisitos
# afectadas salen por stdout para que el shell las instale una a una.
check_dependencies() {
  "$py" -c '
import hashlib
import importlib.util
import re
import sys

requirements, requirements_tui, stamp = sys.argv[1], sys.argv[2], sys.argv[3]

# Distribución -> módulo que debe poder importarse. Sólo las que el deployer
# importa de verdad: jsonschema, web3 y yaml al arrancar, y textual, que
# deployment_v3/*/textual_app.py importa de forma perezosa. Fuera quedan
# py-solc-x, cryptography y python-dotenv: deployment_v3 no los menciona en
# ningún sitio -- los contratos se compilan con la imagen solc-js de
# deployment_v3/runner.py, no con solcx -- así que su ausencia no debe tocar el
# venv. Sólo se comprueban las distribuciones que los ficheros declaran, de modo
# que retirar una dependencia no deja al script reinstalando en cada arranque.
sentinelas = {
    "jsonschema": "jsonschema",
    "pyyaml": "yaml",
    "web3": "web3",
    "textual": "textual",
}

declaradas = []
digest = hashlib.sha256()
for path in (requirements, requirements_tui):
    with open(path, "rb") as handle:
        raw = handle.read()
    digest.update(raw)
    for line in raw.decode("utf-8", "replace").splitlines():
        texto = line.split("#", 1)[0].strip()
        # Fuera comentarios, opciones de pip (-r, -e, --hash) y continuaciones.
        if not texto or texto.startswith("-") or texto.endswith("\\"):
            continue
        nombre = re.split(r"[<>=!~;\[ ]", texto, 1)[0].strip().lower().replace("_", "-")
        if nombre:
            declaradas.append((nombre, texto))
digest.update(sys.version.encode())

faltan_modulos = []
faltan_lineas = []
for nombre, texto in declaradas:
    modulo = sentinelas.get(nombre)
    if modulo is None or importlib.util.find_spec(modulo) is not None:
        continue
    faltan_modulos.append(modulo)
    if texto not in faltan_lineas:
        faltan_lineas.append(texto)
if faltan_modulos:
    print("faltan dependencias: " + ", ".join(sorted(set(faltan_modulos))), file=sys.stderr)

try:
    with open(stamp) as handle:
        registrado = handle.read().strip()
except OSError:
    registrado = ""

# El sello va primero: si los requisitos han cambiado, se reaplica el fichero
# entero y eso ya cubre cualquier módulo que falte. La reparación puntual queda
# para cuando el sello está al día y aun así falta algo.
if registrado != digest.hexdigest():
    print("los requisitos o el intérprete han cambiado desde la última instalación", file=sys.stderr)
    raise SystemExit(8)

if faltan_modulos:
    for texto in faltan_lineas:
        print(texto)
    raise SystemExit(9)

raise SystemExit(0)
' "$requirements" "$requirements_tui" "$stamp"
}

write_stamp() {
  "$py" -c '
import hashlib
import sys

digest = hashlib.sha256()
for path in (sys.argv[1], sys.argv[2]):
    with open(path, "rb") as handle:
        digest.update(handle.read())
digest.update(sys.version.encode())

with open(sys.argv[3], "w") as handle:
    handle.write(digest.hexdigest())
' "$requirements" "$requirements_tui" "$stamp"
}

install_mode=""
reason=""
if [ "$fresh" = "1" ]; then
  install_mode="full"
  reason="venv recién creado"
else
  check_status=0
  repair_specs="$(check_dependencies)" || check_status=$?
  if [ "$check_status" = "0" ]; then
    install_mode="none"
  elif [ "$check_status" = "9" ]; then
    install_mode="repair"
  else
    install_mode="full"
    reason="el sello del venv no cuadra con los ficheros de requisitos"
  fi
fi

case "$install_mode" in
  none) ;;
  full)
    echo "[deploy] instalando dependencias en $venv ($reason)"
    if [ "$fresh" = "1" ]; then
      "$py" -m pip install --quiet --disable-pip-version-check --upgrade pip \
        || echo "[deploy] aviso: no se pudo actualizar pip; sigo con el que hay" >&2
    fi
    if "$py" -m pip install --quiet --disable-pip-version-check \
         -r "$requirements" -r "$requirements_tui"; then
      write_stamp
    else
      echo "[deploy] la instalación de dependencias falló; revisa red/proxy y que haya wheels para este intérprete" >&2
      exit 1
    fi
    ;;
  repair)
    echo "[deploy] reparando en $venv sólo lo que falta (sin reaplicar el fichero entero)"
    repair_failed=0
    while IFS= read -r spec; do
      [ -n "$spec" ] || continue
      echo "[deploy]   $spec"
      # Una reparación no debe quedarse colgada reintentando contra PyPI si no
      # hay red: la orden pedida puede no necesitar ese módulo. No se toca el
      # sello: ya estaba al día, y esta reparación no aplica el fichero entero.
      if ! "$py" -m pip install --quiet --disable-pip-version-check \
           --retries 1 --timeout 15 "$spec"; then
        repair_failed=1
      fi
    done <<EOF
$repair_specs
EOF
    if [ "$repair_failed" != "0" ]; then
      echo "[deploy] aviso: la reparación no se completó; sigo con el venv tal como está" >&2
    fi
    ;;
esac

if [ "$#" -eq 0 ]; then
  echo "[deploy] sin argumentos: te muestro la ayuda del CLI" >&2
  exec "$py" "$here/deploy.py" --help
fi

# El directorio de trabajo del llamante se conserva: deploy.py resuelve las
# rutas relativas contra él. Al pasar la ruta del script, Python pone la raíz del
# repositorio en sys.path[0], así que `deployment_v3` se importa sin PYTHONPATH.
exec "$py" "$here/deploy.py" "$@"
