#!/bin/sh
# Prepare an isolated runtime without changing the system Python or shell profile.
set -eu
umask 077

case "$(uname -s)" in
  Darwin|Linux) ;;
  *) echo "Use launch.ps1 on Windows; this launcher targets macOS and Linux." >&2; exit 1 ;;
esac

runtime_dir="${SCHOLAR_RUNTIME_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/scholar-mcp/runtime}"
package="${SCHOLAR_PACKAGE:-scholar-mcp[rerank]==0.8.5}"
constraints="${SCHOLAR_CONSTRAINTS:-}"
if [ -z "${SCHOLAR_PACKAGE:-}" ]; then
  constraints="${constraints:-https://github.com/Liyux3/scholar-mcp/releases/download/v0.8.5/runtime-constraints.txt}"
fi
uv_bin="${SCHOLAR_UV:-}"
if [ -z "$uv_bin" ]; then
  uv_bin="$(command -v uv 2>/dev/null || true)"
fi
if [ -z "$uv_bin" ]; then
  uv_bin="$runtime_dir/uv/uv"
  if [ ! -x "$uv_bin" ]; then
    mkdir -p "$runtime_dir/uv"
    installer="$(mktemp "${TMPDIR:-/tmp}/scholar-uv.XXXXXX")"
    trap 'rm -f "$installer"' EXIT
    trap 'exit 1' HUP INT TERM
    if command -v curl >/dev/null 2>&1; then
      curl --fail --silent --show-error --location --retry 2 https://astral.sh/uv/install.sh -o "$installer"
    elif command -v wget >/dev/null 2>&1; then
      wget -q https://astral.sh/uv/install.sh -O "$installer"
    else
      echo "An HTTPS downloader (curl or wget) is needed to prepare the runtime." >&2
      exit 1
    fi
    UV_UNMANAGED_INSTALL="$runtime_dir/uv" sh "$installer" >&2
    rm -f "$installer"
    trap - EXIT HUP INT TERM
  fi
fi

# stdout belongs exclusively to MCP. uv setup diagnostics use stderr.
if [ -n "$constraints" ]; then
  exec "$uv_bin" tool run --no-config --managed-python --python 3.12 \
    --constraints "$constraints" --from "$package" scholar-mcp "$@"
fi
exec "$uv_bin" tool run --no-config --managed-python --python 3.12 \
  --from "$package" scholar-mcp "$@"
