#!/usr/bin/env bash
# Daily snapshot of user data (journal, gamification, community, offline).
# cron:  17 3 * * *  /srv/terra/deploy/backup.sh
set -euo pipefail
umask 077  # the archives hold journal data and GPS trails: owner only

DATA_DIR="${TERRA_DATA_DIR:-/srv/terra/backend/data}"
BACKUP_DIR="${TERRA_BACKUP_DIR:-/var/backups/terra}"
KEEP_DAYS="${TERRA_BACKUP_KEEP_DAYS:-30}"

mkdir -p "$BACKUP_DIR"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
tar -czf "$BACKUP_DIR/terra-data-$stamp.tar.gz" -C "$(dirname "$DATA_DIR")" "$(basename "$DATA_DIR")"
find "$BACKUP_DIR" -name 'terra-data-*.tar.gz' -mtime +"$KEEP_DAYS" -delete
echo "backup written: $BACKUP_DIR/terra-data-$stamp.tar.gz"
