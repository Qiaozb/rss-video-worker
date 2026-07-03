#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/game-daily/mysql}"
DATABASE="${MYSQL_DATABASE:-dify_test}"
MYSQL_HOST="${MYSQL_HOST:-127.0.0.1}"
MYSQL_PORT="${MYSQL_PORT:-3306}"
MYSQL_USER="${MYSQL_USER:-root}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

mkdir -p "$BACKUP_DIR"

timestamp="$(date +%Y%m%d-%H%M%S)"
target="$BACKUP_DIR/${DATABASE}-${timestamp}.sql.gz"

mysqldump \
  --host="$MYSQL_HOST" \
  --port="$MYSQL_PORT" \
  --user="$MYSQL_USER" \
  --single-transaction \
  --routines \
  --triggers \
  "$DATABASE" | gzip > "$target"

find "$BACKUP_DIR" -type f -name "${DATABASE}-*.sql.gz" -mtime +"$RETENTION_DAYS" -delete

echo "$target"
