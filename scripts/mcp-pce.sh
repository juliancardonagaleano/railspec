#!/usr/bin/env sh
# Lanza el cliente MCP de la PCE (`pce-mcp`) leyendo la credencial de `.env`
# (git-ignored), la misma fuente que usa el contenedor del orquestador.
#
# Este loader es parte de la **carga del kit**: `.mcp.json` (Claude) y
# `opencode.jsonc` (Opencode) lo invocan desde el destino vía `command: sh
# scripts/mcp-pce.sh`. La diferencia con `iark/scripts/mcp-pce.sh` es
# genérica — el script no conoce a qué ecosistema pertenece el caller; solo
# carga `.env` y exec'a `scripts/mcp-pce.py`.
#
# `.mcp.json` original expandía `${PCE_MCP_API_KEY}` desde el entorno del
# proceso de Claude Code: si el shell que lo arrancó no la exportaba, el
# header llegaba vacío y el router atendía como principal ANÓNIMO — sin 401,
# solo con la gobernanza `internal` omitida (unidad 0107, research.md § F
# de iark). Aquí la fuente es el archivo `.env`, no el shell.
#
# Precondiciones en el destino:
#   - `.env` existe y define `PCE_MCP_API_KEY` (y opcionalmente `PCE_MCP_URL`).
#   - `scripts/mcp-pce.py` existe (provisto por la PCE; no es carga del kit).
# Si alguna falta, el script falla con código != 0 y mensaje claro a stderr
# — los hooks `PreToolUse` que lo invoquen deben tratarlo como fail-open
# (ver CA-54 de la unit `0002-versionado-y-actualizacion-del-instalador`).
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/.env"

[ -f "$ENV_FILE" ] || { echo "mcp-pce: no existe $ENV_FILE" >&2; exit 1; }

KEY="$(grep '^PCE_MCP_API_KEY=' "$ENV_FILE" | head -n 1 | cut -d= -f2- | sed -e 's/^["'"'"']//' -e 's/["'"'"']$//')"
[ -n "$KEY" ] || { echo "mcp-pce: PCE_MCP_API_KEY vacía en $ENV_FILE" >&2; exit 1; }

URL="$(grep '^PCE_MCP_URL=' "$ENV_FILE" | head -n 1 | cut -d= -f2- | sed -e 's/^["'"'"']//' -e 's/["'"'"']$//')"
[ -n "$URL" ] || URL="https://20.7.84.154/mcp"

exec python3 "$(dirname "$0")/mcp-pce.py"
