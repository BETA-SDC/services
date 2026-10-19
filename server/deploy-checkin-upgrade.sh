#!/bin/sh
# Add check-in to the shared account service; do not change network listeners.
set -eu
umask 077
[ "$(id -u)" = 0 ] && [ "$(hostname)" = 550c ] || exit 1
SOURCE=${1:?Provide the staged account source}
WHEEL=${2:?Provide the pinned qrcode wheel}
ROOT=/opt/betasdc
PYTHON=/opt/betasdc-calendar/.venv/bin/python
BACKUP=$(mktemp -d /var/backups/betasdc/checkin-upgrade-XXXXXX)
FILES="server/registration/__init__.py server/registration/repository.py server/registration/private_data.py server/registration/security.py server/registration/checkin.py server/purge_checkins.py server/test_checkin.py server/requirements-checkin.txt server/CHECKIN.md server/deploy-checkin-upgrade.sh THIRD-PARTY.md"
ASSETS="checkin.html checkin.js checkin.css beta-shell.js privacy.html"
backup() {
    target=$1
    mkdir -p "$BACKUP$(dirname "$target")"
    if [ -f "$target" ]; then cp -p "$target" "$BACKUP$target"
    else printf '%s\n' "$target" >> "$BACKUP/missing"; fi
    printf '%s\n' "$target" >> "$BACKUP/manifest"
}
for file in $FILES; do
    [ -f "$SOURCE/$file" ]
    backup "$ROOT/$file"
done
for file in $ASSETS; do
    [ -f "$SOURCE/campus-register/$file" ]
    backup "$ROOT/static/$file"
done
"$PYTHON" -B -c '
import sqlite3,sys
from contextlib import closing
with closing(sqlite3.connect("file:/var/lib/betasdc/registrations.sqlite3?mode=ro", uri=True)) as source:
    with closing(sqlite3.connect(sys.argv[1])) as target:
        source.backup(target)
        assert target.execute("PRAGMA quick_check").fetchone() == ("ok",)
' "$BACKUP/database.sqlite3"
rollback() {
    result=$?
    if [ "$result" != 0 ]; then
        while IFS= read -r target; do
            if [ -f "$BACKUP$target" ]; then cp -p "$BACKUP$target" "$target"; fi
        done < "$BACKUP/manifest"
        if [ -f "$BACKUP/missing" ]; then
            while IFS= read -r target; do rm -f "$target"; done < "$BACKUP/missing"
        fi
        systemctl restart betasdc.service
        printf 'Check-in upgrade failed; restored code. Database not rolled back. Backup: %s\n' "$BACKUP" >&2
    fi
}
trap rollback EXIT
(
    umask 022
    if ! PYTHONPATH="$ROOT/server/_vendor" "$PYTHON" -c 'from importlib.metadata import version; assert version("qrcode") == "8.2"' 2>/dev/null; then
        "$PYTHON" -m pip install --no-index --no-deps --target "$ROOT/server/_vendor" "$WHEEL"
    fi
)
for file in $FILES; do install -o root -g root -m 644 "$SOURCE/$file" "$ROOT/$file"; done
for file in $ASSETS; do install -o root -g root -m 644 "$SOURCE/campus-register/$file" "$ROOT/static/$file"; done
cd "$ROOT"
runuser -u betasdc -g www-data -- env PYTHONPATH="$ROOT" "$PYTHON" -B -m unittest server.test_checkin server.test_sites
cd /opt/betasdc-calendar
runuser -u betasdc -g www-data -- env CALENDAR_STATE=/var/lib/betasdc-calendar PYTHONPATH="$ROOT" \
    "$PYTHON" -B -c 'import sys; sys.path.insert(0,"/opt/betasdc"); from calendar_service.beta_entry import create_app; create_app(); print("Shared applications initialized")'
systemctl kill --kill-who=main --signal=HUP betasdc.service
sleep 8
systemctl is-active --quiet betasdc.service betasdc-calendar.service betasdc-nginx.service
curl --noproxy '*' --retry 5 --retry-all-errors --retry-delay 2 --connect-timeout 3 --max-time 15 -fsS \
    --resolve account.betasdc.duckdns.org:443:10.16.125.52 \
    https://account.betasdc.duckdns.org/api/checkin/session |
    "$PYTHON" -c 'import json,sys; d=json.load(sys.stdin); assert d["policy"]=="checkin-2026-10-16-v3" and not d["can_manage"]'
systemctl disable --now betasdc-checkin-cleanup.timer
systemctl stop betasdc-checkin-cleanup.service
printf 'Check-in deployed; attendance auto-deletion disabled. Backup: %s\n' "$BACKUP"
trap - EXIT
