#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.dify-rss.video-worker"
DOMAIN="gui/$(id -u)"
SOURCE_PLIST="$ROOT_DIR/launchd/$LABEL.plist"
INSTALLED_PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

cd "$ROOT_DIR"
mkdir -p logs run output "$HOME/Library/LaunchAgents"

while IFS= read -r pid; do
  [[ -z "$pid" ]] && continue
  cwd="$(lsof -nP -a -p "$pid" -d cwd -Fn 2>/dev/null | awk '/^n/{print substr($0, 2); exit}')"
  if [[ "$cwd" != "$ROOT_DIR" ]]; then
    echo "8000 端口已被其他进程占用，PID $pid，工作目录：${cwd:-unknown}" >&2
    echo "请先释放端口后再启动 video-worker。" >&2
    exit 1
  fi

  echo "发现残留 video-worker 进程 PID ${pid}，启动前清理。"
  kill "$pid" >/dev/null 2>&1 || true
  for _ in {1..20}; do
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      break
    fi
    sleep 0.2
  done
  if kill -0 "$pid" >/dev/null 2>&1; then
    kill -9 "$pid" >/dev/null 2>&1 || true
  fi
done < <(lsof -tiTCP:8000 -sTCP:LISTEN 2>/dev/null || true)

if [[ ! -x .venv/bin/python ]]; then
  echo "尚未安装本机依赖，请先执行: ./scripts/setup-local.sh" >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  echo "缺少 .env，请复制 .env.example 并填写数据库配置。" >&2
  exit 1
fi

# The checked-in plist is a portable template. Materialize checkout-specific
# paths on every start so moving or renaming the repository cannot leave
# launchd pointing at an old checkout.
.venv/bin/python scripts/render-launchd-plist.py \
  "$SOURCE_PLIST" "$INSTALLED_PLIST" "$ROOT_DIR"

# launchd does not reload an already bootstrapped plist from disk. Remove the
# old registration first, then bootstrap the freshly materialized config.
if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
  launchctl bootout "$DOMAIN/$LABEL"
fi
launchctl bootstrap "$DOMAIN" "$INSTALLED_PLIST"

for _ in {1..30}; do
  if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
    PID="$(launchctl print "$DOMAIN/$LABEL" | awk '/pid =/{print $3; exit}')"
    echo "video-worker 已由 launchd 启动，PID ${PID:-unknown}"
    echo "健康检查: http://127.0.0.1:8000/health"
    echo "日志目录: $ROOT_DIR/logs"
    exit 0
  fi
  sleep 1
done

echo "video-worker 启动失败，最近日志：" >&2
tail -n 40 "$ROOT_DIR/logs/video-worker-error.log" >&2 || true
tail -n 40 "$ROOT_DIR/logs/video-worker.log" >&2 || true
exit 1
