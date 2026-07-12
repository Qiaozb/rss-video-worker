#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/game-daily/video-worker}"
OUTPUT_RETENTION_DAYS="${OUTPUT_RETENTION_DAYS:-30}"
AUDIO_RETENTION_DAYS="${AUDIO_RETENTION_DAYS:-14}"

find "$APP_DIR/output" -type f -name "*.mp4" -mtime +"$OUTPUT_RETENTION_DAYS" -delete 2>/dev/null || true
find "$APP_DIR/output" -type f -name "props.json" -mtime +"$OUTPUT_RETENTION_DAYS" -delete 2>/dev/null || true
find "$APP_DIR/output" -type f -path "*/report_*/audio/*.wav" -mtime +"$AUDIO_RETENTION_DAYS" -delete 2>/dev/null || true

find "$APP_DIR/output" -type d -empty -delete 2>/dev/null || true
