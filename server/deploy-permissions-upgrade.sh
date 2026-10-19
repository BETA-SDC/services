#!/bin/sh
# Scoped account-only upgrade; retain shared Gunicorn and Nginx configuration.
set -eu
[ "$(id -u)" = 0 ] || { printf '%s\n' "Run as root on the Linux server."; exit 1; }
[ "$(hostname)" = 550c ] || { printf '%s\n' "Unexpected deployment host."; exit 1; }
SOURCE=${1:?Provide the staged source directory}
SNAPSHOT=$(mktemp -d /var/backups/betasdc/account-upgrade-20261015-XXXXXX)
chmod 700 "$SNAPSHOT"
FILES="server/registration/__init__.py server/registration/auth.py server/registration/github.py server/registration/repository.py server/registration/routes.py server/registration/permissions.py server/registration/profile.py server/registration/validation.py server/registration/signup.py server/registration/security.py server/github_broker.py server/github_profile.py server/github_organization.py server/sync_github_profiles.py server/manage_admin.py server/test_github.py server/test_permissions.py server/test_webapp.py server/test_profile.py server/test_signup.py server/test_support.py static/index.html static/main.js static/styles.css static/admin.html static/admin.js static/admin.css"
for file in $FILES; do
    original="$SOURCE/$file"
    case "$file" in static/*) original="$SOURCE/campus-register/${file#static/}" ;; esac
    [ -f "$original" ] || { printf 'Missing source: %s\n' "$file"; exit 1; }
    if [ -f "/opt/betasdc/$file" ]; then
        install -d -m 700 "$SNAPSHOT/$(dirname "$file")"
        cp -p "/opt/betasdc/$file" "$SNAPSHOT/$file"
    fi
done
python3 -B -c '
import sqlite3, sys
from contextlib import closing
with closing(sqlite3.connect("/var/lib/betasdc/registrations.sqlite3")) as source:
    with closing(sqlite3.connect(sys.argv[1])) as target:
        source.backup(target)
' "$SNAPSHOT/database.sqlite3"
chmod 600 "$SNAPSHOT/database.sqlite3"
rollback() {
    result=$?
    if [ "$result" != 0 ]; then
        for file in $FILES; do
            if [ -f "$SNAPSHOT/$file" ]; then
                cp -p "$SNAPSHOT/$file" "/opt/betasdc/$file"
            else
                rm -f "/opt/betasdc/$file"
            fi
        done
        systemctl restart betasdc-github.service
        systemctl restart betasdc.service
        printf 'Upgrade failed; code restored. Backup: %s\n' "$SNAPSHOT" >&2
    fi
}
trap rollback EXIT
for file in $FILES; do
    original="$SOURCE/$file"
    case "$file" in static/*) original="$SOURCE/campus-register/${file#static/}" ;; esac
    install -o root -g root -m 644 "$original" "/opt/betasdc/$file"
done
systemctl restart betasdc-github.service
systemctl kill --kill-who=main --signal=HUP betasdc.service
sleep 3
systemctl is-active --quiet betasdc.service betasdc-github.service
curl -fsS --resolve account.betasdc.duckdns.org:443:10.16.125.52 \
    https://account.betasdc.duckdns.org/api/health >/dev/null
status=$(curl -sS -o /dev/null -w '%{http_code}' \
    --resolve account.betasdc.duckdns.org:443:10.16.125.52 \
    https://account.betasdc.duckdns.org/api/admin/overview)
[ "$status" = 401 ]
printf 'Account permissions upgrade complete. Backup: %s\n' "$SNAPSHOT"
trap - EXIT
