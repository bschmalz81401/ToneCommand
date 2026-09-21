#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
VERSION="$($PYTHON -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])' 2>/dev/null || true)"
if [[ -z "$VERSION" ]]; then
  echo "Could not read project version from pyproject.toml" >&2
  exit 1
fi

cd "$ROOT"
if ! "$PYTHON" -c 'import PyInstaller' >/dev/null 2>&1; then
  echo "PyInstaller is required; install it in the selected Python environment" >&2
  exit 1
fi

ENTRYPOINT="${TONECOMMAND_ENTRYPOINT:-$ROOT/.venv/bin/tonecommand}"
if [[ ! -x "$ENTRYPOINT" ]]; then
  ENTRYPOINT="$(command -v tonecommand || true)"
fi
if [[ -z "$ENTRYPOINT" || ! -x "$ENTRYPOINT" ]]; then
  echo "The installed tonecommand entry point is required" >&2
  exit 1
fi

export PYINSTALLER_CONFIG_DIR="${PYINSTALLER_CONFIG_DIR:-$ROOT/.pyinstaller}"
rm -rf build dist
"$PYTHON" -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --specpath "$ROOT/build" \
  --name ToneCommand \
  --paths "$ROOT" \
  --add-data "$ROOT/ui:ui" \
  --add-data "$ROOT/config:config" \
  --add-data "$ROOT/recipes:recipes" \
  --collect-all rtmidi \
  --collect-all sounddevice \
  --collect-submodules mido.backends \
  --hidden-import server \
  "$ENTRYPOINT"

echo "BUILT_APP=$ROOT/dist/ToneCommand.app"
echo "BUNDLE_VERSION=$VERSION"
