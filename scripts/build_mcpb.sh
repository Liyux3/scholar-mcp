#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON:-python3.12}"
BUILD_DIR="$ROOT/mcpb-build"

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

rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR/server/vendor"

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

REQUIREMENTS="$BUILD_DIR/requirements.txt"
uv export --quiet --locked --no-dev --no-emit-project \
  --format requirements-txt --output-file "$REQUIREMENTS"
uv pip install --quiet --python "$PYTHON_BIN" --target "$BUILD_DIR/server/vendor" \
  --requirement "$REQUIREMENTS"
rm "$REQUIREMENTS"
find "$BUILD_DIR" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$BUILD_DIR" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete

cd "$BUILD_DIR"
npx --yes @anthropic-ai/mcpb@2.1.2 validate manifest.json
npx --yes @anthropic-ai/mcpb@2.1.2 pack

ARTIFACT="$(find "$BUILD_DIR" -maxdepth 1 -name '*.mcpb' -print -quit)"
test -n "$ARTIFACT"
FINAL="$BUILD_DIR/scholar-mcp-darwin-$(uname -m)-$VERSION.mcpb"
if [[ "$ARTIFACT" != "$FINAL" ]]; then
  mv "$ARTIFACT" "$FINAL"
fi
echo "$FINAL"
