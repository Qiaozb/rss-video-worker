#!/usr/bin/env bash
set -euo pipefail

LABEL="com.dify-rss.video-worker"
DOMAIN="gui/$(id -u)"

if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
  PID="$(launchctl print "$DOMAIN/$LABEL" | awk '/pid =/{print $3; exit}')"
  echo "launchd 状态: running (PID ${PID:-unknown})"
else
  echo "launchd 状态: stopped"
fi

if curl -fsS http://127.0.0.1:8000/health; then
  echo
else
  echo "接口状态: unavailable"
fi
