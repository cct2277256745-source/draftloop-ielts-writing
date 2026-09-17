#!/usr/bin/env bash
# 一键创建虚拟环境、安装依赖并启动雅思写作批改 App
set -e

cd "$(dirname "$0")"

VENV=".venv"
PY="$VENV/bin/python"

if [ ! -d "$VENV" ]; then
  echo "==> 创建虚拟环境 $VENV"
  python3 -m venv "$VENV"
fi

echo "==> 升级 pip 并安装依赖"
"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet -r requirements.txt

echo "==> 启动 IELTS 写作批改 App"
exec "$PY" -m app.main
