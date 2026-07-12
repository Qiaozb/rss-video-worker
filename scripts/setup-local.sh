#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3.11}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "未找到 $PYTHON_BIN，请先执行: brew install python@3.11" >&2
  exit 1
fi

if ! command -v node >/dev/null 2>&1; then
  echo "未找到 Node.js，请先安装 Node.js 20 或更高版本。" >&2
  exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  echo "未找到 FFmpeg，请先执行: brew install ffmpeg" >&2
  exit 1
fi

if [[ ! -d .venv ]]; then
  "$PYTHON_BIN" -m venv .venv
fi

.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
npm install --legacy-peer-deps --no-audit --no-fund

mkdir -p output logs run

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "已创建 .env，请填写 MYSQL_PASSWORD 后再启动。"
fi

echo "本机依赖安装完成。"
