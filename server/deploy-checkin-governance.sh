#!/bin/sh
# Attendance governance only: no listener, network or unrelated service changes.
set -eu
umask 077
[ "$(id -u)" = 0 ] && [ "$(hostname)" = 550c ] || exit 1
SOURCE=${1:?Provide staged source}
ROOT=/opt/betasdc
PYTHON=/opt/betasdc-calendar/.venv/bin/python
release_paths() {
    "$PYTHON" -B - "$SOURCE/server/checkin-release.json" "$1" <<'PY'
import json
from pathlib import PurePosixPath
import re
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    paths = json.load(stream)[sys.argv[2]]
assert isinstance(paths, list) and paths
assert len(set(paths)) == len(paths)
assert all(isinstance(path, str) and re.fullmatch(r"[A-Za-z0-9_./-]+", path)
           and not PurePosixPath(path).is_absolute()
           and ".." not in PurePosixPath(path).parts for path in paths)
print(" ".join(paths))
PY
}
FILES=$(release_paths files)
ASSETS=$(release_paths assets)
UNITS=$(release_paths units)
# Validate the complete release before changing any running service.
for file in $FILES; do [ -f "$SOURCE/$file" ]; done
for file in $ASSETS; do [ -f "$SOURCE/campus-register/$file" ]; done
for unit in $UNITS; do [ -f "$SOURCE/server/$unit" ]; done
BACKUP=$(mktemp -d /var/backups/betasdc/checkin-governance-XXXXXX)
backup() {
    mkdir -p "$BACKUP$(dirname "$1")"
    if [ -f "$1" ]; then cp -p "$1" "$BACKUP$1"
    else printf '%s\n' "$1" >> "$BACKUP/missing"; fi
    printf '%s\n' "$1" >> "$BACKUP/manifest"
}
systemctl disable --now betasdc-checkin-cleanup.timer
systemctl stop betasdc-checkin-cleanup.service
MAIL_TIMER_ACTIVE=$(systemctl is-active betasdc-mail.timer || true)
for file in $FILES; do [ -f "$SOURCE/$file" ]; backup "$ROOT/$file"; done
for file in $ASSETS; do [ -f "$SOURCE/campus-register/$file" ]; backup "$ROOT/static/$file"; done
for unit in $UNITS; do [ -f "$SOURCE/server/$unit" ]; backup "/etc/systemd/system/$unit"; done
"$PYTHON" -B - "$BACKUP/database.sqlite3" <<'PY'
from contextlib import closing
import sqlite3
import sys

with closing(sqlite3.connect("file:/var/lib/betasdc/registrations.sqlite3?mode=ro", uri=True)) as source:
    with closing(sqlite3.connect(sys.argv[1])) as target:
        source.backup(target)
        assert target.execute("PRAGMA quick_check").fetchone() == ("ok",)
    print("Existing attendance consent versions:",
          source.execute("SELECT policy,count(*) FROM checkin_entries GROUP BY policy").fetchall())
PY
rollback() {
    result=$?
    if [ "$result" != 0 ]; then
        while IFS= read -r target; do
            if [ -f "$BACKUP$target" ]; then cp -p "$BACKUP$target" "$target"; fi
        done < "$BACKUP/manifest"
        if [ -f "$BACKUP/missing" ]; then
            while IFS= read -r target; do rm -f "$target"; done < "$BACKUP/missing"
        fi
        systemctl daemon-reload
        systemctl restart betasdc.service
        if [ "$MAIL_TIMER_ACTIVE" = active ]; then systemctl start betasdc-mail.timer; fi
        printf 'Governance upgrade rolled back code; timer stays disabled, database preserved. Backup: %s\n' "$BACKUP" >&2
    fi
}
trap rollback EXIT
systemctl stop betasdc-mail.timer betasdc-mail.service
# Protect even old workers and a code-only rollback from age-based deletion.
"$PYTHON" -B - <<'PY'
from contextlib import closing
import sqlite3
with closing(sqlite3.connect("/var/lib/betasdc/registrations.sqlite3")) as connection, connection:
    connection.execute("UPDATE checkin_events SET retain_until=253402300799 WHERE retain_until<>253402300799")
PY
for file in $FILES; do
    install -d -o root -g root -m 755 "$(dirname "$ROOT/$file")"
    install -o root -g root -m 644 "$SOURCE/$file" "$ROOT/$file"
done
for file in $ASSETS; do
    install -d -o root -g root -m 755 "$(dirname "$ROOT/static/$file")"
    install -o root -g root -m 644 "$SOURCE/campus-register/$file" "$ROOT/static/$file"
done
for unit in $UNITS; do install -o root -g root -m 644 "$SOURCE/server/$unit" "/etc/systemd/system/$unit"; done
systemctl daemon-reload
cd "$ROOT"
runuser -u betasdc -g www-data -- "$PYTHON" -B -m unittest server.test_checkin server.test_checkin_access server.test_checkin_architecture server.test_profile server.test_sites server.test_network_policy server.test_verification server.test_second_factor server.test_signup
cd /opt/betasdc-calendar
runuser -u betasdc -g www-data -- env CALENDAR_STATE=/var/lib/betasdc-calendar "$PYTHON" -B -c '
import sys
sys.path.insert(0,"/opt/betasdc")
from calendar_service.beta_entry import create_app
app=create_app()
print("Shared app initialized; attendance governance schema ready")
'
systemctl kill --kill-who=main --signal=HUP betasdc.service
if [ "$MAIL_TIMER_ACTIVE" = active ]; then systemctl start betasdc-mail.timer; fi
sleep 8
systemctl is-active --quiet betasdc.service betasdc-nginx.service betasdc-calendar.service
if systemctl is-active --quiet betasdc-checkin-cleanup.timer; then exit 1; fi
if systemctl is-enabled --quiet betasdc-checkin-cleanup.timer; then exit 1; fi
curl --noproxy '*' --retry 5 --retry-all-errors --retry-delay 2 --connect-timeout 3 --max-time 15 -fsS \
    --resolve account.betasdc.duckdns.org:443:10.16.125.52 \
    https://account.betasdc.duckdns.org/api/checkin/session |
    "$PYTHON" -c 'import json,sys; d=json.load(sys.stdin); assert d["policy"]=="checkin-2026-10-19-v5" and not d["can_manage"] and not d["can_create"]'
printf 'Attendance governance deployed; no automatic deletion. Backup: %s\n' "$BACKUP"
trap - EXIT
