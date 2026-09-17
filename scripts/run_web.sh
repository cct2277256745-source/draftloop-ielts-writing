#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -x .venv/bin/python ]]; then
  echo '缺少 Python 环境，请先安装项目依赖。' >&2
  exit 1
fi
npm run build --prefix frontend
# Reuse the explicitly configured private local RAG environment when present.
# This file is ignored by Git and never enters the frontend bundle.
if [[ -f .scratch/local-rag/runtime.env ]]; then
  source .scratch/local-rag/runtime.env
fi
exec .venv/bin/python -u -m app.product_composition.local_browser \
  --data-root .scratch/browser-c3/runtime --allow-local-session "$@"
