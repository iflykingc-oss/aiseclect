#!/usr/bin/env bash
# 本地运行入口
# 依赖：uv（推荐）或 pip 装好 .venv
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

# 优先用 uv 创建/同步 .venv
if command -v uv >/dev/null 2>&1; then
  if [ ! -d ".venv" ]; then
    uv venv
  fi
  # shellcheck disable=SC1091
  source .venv/bin/activate
  uv sync
else
  echo "[warn] uv 未安装，使用系统 python"
fi

exec python -m src.main "$@"
