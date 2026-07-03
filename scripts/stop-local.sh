#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.dify-rss.video-worker"
DOMAIN="gui/$(id -u)"

if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
  launchctl bootout "$DOMAIN/$LABEL"
  echo "本机 video-worker 已停止。"
else
  echo "本机 video-worker 未运行。"
fi

while IFS= read -r pid; do
  [[ -z "$pid" ]] && continue
  cwd="$(lsof -nP -a -p "$pid" -d cwd -Fn 2>/dev/null | awk '/^n/{print substr($0, 2); exit}')"
  if [[ "$cwd" != "$ROOT_DIR" ]]; then
    echo "跳过 8000 端口进程 PID ${pid}：工作目录不是 video-worker。"
    continue
  fi

  echo "清理残留 video-worker 进程 PID ${pid}。"
  kill "$pid" >/dev/null 2>&1 || true
  for _ in {1..20}; do
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      break
    fi
    sleep 0.2
  done
  if kill -0 "$pid" >/dev/null 2>&1; then
    echo "残留进程 PID ${pid} 未正常退出，强制终止。"
    kill -9 "$pid" >/dev/null 2>&1 || true
  fi
done < <(lsof -tiTCP:8000 -sTCP:LISTEN 2>/dev/null || true)
