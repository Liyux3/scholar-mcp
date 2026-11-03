#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON:-python3.12}"
BUILD_ROOT="$ROOT/mcpb-build"
mkdir -p "$BUILD_ROOT"
BUILD_DIR="$(mktemp -d "$BUILD_ROOT/stage.XXXXXX")"

cleanup_stage() {
  "$PYTHON_BIN" - "$BUILD_DIR" "$BUILD_ROOT" <<'PY'
from pathlib import Path
import shutil, sys
stage, root = (Path(value).resolve() for value in sys.argv[1:])
if stage.parent != root or not stage.name.startswith("stage."):
    raise SystemExit("Refusing to clean an unexpected staging directory")
shutil.rmtree(stage, ignore_errors=True)
PY
}
trap cleanup_stage EXIT

"$PYTHON_BIN" - <<'PY'
import sys
if sys.version_info[:2] != (3, 12) or sys.platform != "darwin":
    raise SystemExit("MCPB builds require CPython 3.12 on macOS")
PY

VERSION="$("$PYTHON_BIN" - "$ROOT/pyproject.toml" <<'PY'
from pathlib import Path
import sys, tomllib
print(tomllib.loads(Path(sys.argv[1]).read_text())["project"]["version"])
PY
)"

mkdir -p "$BUILD_DIR/server/vendor"

# Bundle a relocatable interpreter, not the builder's system Python. This
# makes the desktop artifact runnable without Python, uv or shell setup.
UV_PYTHON_INSTALL_DIR="$BUILD_DIR/managed" uv python install --no-bin 3.12 >&2
RUNTIME_PYTHON="$(UV_PYTHON_INSTALL_DIR="$BUILD_DIR/managed" uv python find --no-project --system --managed-python 3.12)"
"$RUNTIME_PYTHON" - "$BUILD_DIR" <<'PY'
from pathlib import Path
import shutil, sys
stage = Path(sys.argv[1]).resolve()
source = Path(sys.base_prefix).resolve()
if not source.is_relative_to(stage / "managed"):
    raise SystemExit("Expected the isolated managed interpreter")
# MCPB extraction must not depend on symlink support or host absolute paths.
shutil.copytree(source, stage / "runtime", symlinks=False,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
shutil.rmtree(stage / "managed")
PY
RUNTIME_PYTHON="$BUILD_DIR/runtime/bin/python3.12"

"$PYTHON_BIN" - "$ROOT/manifest.json" "$BUILD_DIR/manifest.json" "$VERSION" <<'PY'
import json, sys
source, target, version = sys.argv[1:]
manifest = json.loads(open(source, encoding="utf-8").read())
manifest["version"] = version
with open(target, "w", encoding="utf-8") as output:
    json.dump(manifest, output, indent=2)
    output.write("\n")
PY

cp -R "$ROOT/scholar_mcp" "$BUILD_DIR/server/scholar_mcp"
cp "$ROOT/LICENSE" "$ROOT/NOTICE" "$BUILD_DIR/"

REQUIREMENTS="$BUILD_DIR/requirements.txt"
uv export --project "$ROOT" --quiet --locked --no-hashes --no-dev --no-emit-project --extra rerank \
  --format requirements-txt --output-file "$REQUIREMENTS"
uv pip install --quiet --python "$PYTHON_BIN" --target "$BUILD_DIR/server/vendor" \
  --requirement "$REQUIREMENTS"
rm "$REQUIREMENTS"

# Keep a verified CPU reader in the bundle, separate from writable user data.
SCHOLAR_DATA_DIR="$BUILD_DIR/.prepare-data" "$RUNTIME_PYTHON" - "$BUILD_DIR" "${SCHOLAR_MODEL_ARCHIVE:-}" <<'PY'
from pathlib import Path
import sys
stage = Path(sys.argv[1])
sys.path[:0] = [str(stage / 'server'), str(stage / 'server/vendor')]
from scholar_mcp.model_assets import prepare_cpu_model
prepare_cpu_model(stage / 'server/scholar_mcp/_models',
                  archive=Path(sys.argv[2]) if sys.argv[2] else None)
PY
find "$BUILD_DIR" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$BUILD_DIR" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete

cd "$BUILD_DIR"
npx --yes @anthropic-ai/mcpb@2.1.2 validate manifest.json
npx --yes @anthropic-ai/mcpb@2.1.2 pack

ARTIFACT="$(find "$BUILD_DIR" -maxdepth 1 -name '*.mcpb' -print -quit)"
test -n "$ARTIFACT"
FINAL="$BUILD_ROOT/scholar-mcp-darwin-$(uname -m)-$VERSION.mcpb"
if [[ "$ARTIFACT" != "$FINAL" ]]; then
  mv "$ARTIFACT" "$FINAL"
fi
echo "$FINAL"
