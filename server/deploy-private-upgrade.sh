#!/bin/sh
# Account-only code/dependency upgrade, preserving ports, units and calendar.
set -eu
umask 077
[ "$(id -u)" = 0 ] || { printf '%s\n' "Run as root."; exit 1; }
[ "$(hostname)" = 550c ] || { printf '%s\n' "Unexpected deployment host."; exit 1; }
SOURCE=${1:?Provide the staged source directory}
SNAPSHOT=$(mktemp -d /var/backups/betasdc/private-upgrade-20261015-XXXXXX)
chmod 700 "$SNAPSHOT"
FILES="server/registration/__init__.py server/registration/repository.py server/registration/routes.py server/registration/private_data.py server/registration/private_validation.py server/registration/passkeys.py server/registration/second_factor.py server/mail_worker.py server/test_private.py server/test_second_factor.py server/requirements-private.txt static/index.html static/main.js static/settings.js static/private.js static/privacy.html static/styles.css"
for file in $FILES; do
    original="$SOURCE/$file"
    case "$file" in static/*) original="$SOURCE/campus-register/${file#static/}" ;; esac
    [ -f "$original" ] || { printf 'Missing source: %s\n' "$file"; exit 1; }
    if [ -f "/opt/betasdc/$file" ]; then
        install -d -m 700 "$SNAPSHOT/$(dirname "$file")"
        cp -p "/opt/betasdc/$file" "$SNAPSHOT/$file"
    fi
done
[ -d "$SOURCE/server/_vendor" ] || { printf '%s\n' "Install pinned dependencies in staged server/_vendor first."; exit 1; }
if [ -d /opt/betasdc/server/_vendor ]; then
    cp -a /opt/betasdc/server/_vendor "$SNAPSHOT/vendor"
fi
python3 -B -c '
import sqlite3, sys
from contextlib import closing
with closing(sqlite3.connect("/var/lib/betasdc/registrations.sqlite3")) as source:
    duplicates = source.execute("SELECT count(*) FROM (SELECT lower(email) FROM registrations GROUP BY lower(email) HAVING count(*)>1)").fetchone()[0]
    if duplicates:
        raise RuntimeError("Resolve duplicate email identities before migration")
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
        rm -rf /opt/betasdc/server/_vendor
        if [ -d "$SNAPSHOT/vendor" ]; then cp -a "$SNAPSHOT/vendor" /opt/betasdc/server/_vendor; fi
        systemctl restart betasdc.service
        printf 'Upgrade failed; code restored. Backup: %s\n' "$SNAPSHOT" >&2
    fi
}
trap rollback EXIT
rm -rf /opt/betasdc/server/_vendor
cp -a "$SOURCE/server/_vendor" /opt/betasdc/server/_vendor
chown -R root:root /opt/betasdc/server/_vendor
chmod -R u=rwX,go=rX /opt/betasdc/server/_vendor
for file in $FILES; do
    original="$SOURCE/$file"
    case "$file" in static/*) original="$SOURCE/campus-register/${file#static/}" ;; esac
    install -o root -g root -m 644 "$original" "/opt/betasdc/$file"
done
cd /opt/betasdc
runuser -u betasdc -g www-data -- /opt/betasdc-calendar/.venv/bin/python -B -c \
    'from server.registration import create_app; create_app(); print("Private schema and encryption key initialized")'
install -d -m 700 /var/backups/betasdc/keys
if [ ! -f /var/backups/betasdc/keys/registrations.private-key ]; then
    install -o root -g root -m 600 /var/lib/betasdc/registrations.private-key \
        /var/backups/betasdc/keys/registrations.private-key
fi
cmp -s /var/lib/betasdc/registrations.private-key /var/backups/betasdc/keys/registrations.private-key
systemctl kill --kill-who=main --signal=HUP betasdc.service
sleep 3
systemctl is-active --quiet betasdc.service betasdc-nginx.service
curl -fsS --resolve account.betasdc.duckdns.org:443:10.16.125.52 \
    https://account.betasdc.duckdns.org/api/health >/dev/null
curl -fsS --resolve account.betasdc.duckdns.org:443:10.16.125.52 \
    https://account.betasdc.duckdns.org/privacy >/dev/null
status=$(curl -sS -o /dev/null -w '%{http_code}' \
    --resolve account.betasdc.duckdns.org:443:10.16.125.52 \
    https://account.betasdc.duckdns.org/api/private)
[ "$status" = 401 ]
printf 'Private data upgrade complete. Backup: %s\n' "$SNAPSHOT"
trap - EXIT
