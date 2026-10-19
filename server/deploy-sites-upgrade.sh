#!/bin/sh
# Upgrade shared navigation without changing listeners, units or event data.
set -eu
umask 077
[ "$(id -u)" = 0 ] && [ "$(hostname)" = 550c ] || exit 1
SOURCE=${1:?Provide staged account and calendar directories}
BACKUP=$(mktemp -d /var/backups/betasdc/sites-upgrade-20261016-XXXXXX)
ACCOUNT_FILES="server/registration/__init__.py server/registration/auth.py server/registration/config.py server/registration/repository.py server/registration/security.py server/registration/signup.py server/registration/sites.py server/registration/private_data.py server/test_sites.py server/README.md server/install.sh"
ASSETS="index.html main.js settings.js styles.css admin.html admin.js beta-shell.js beta-shell.css navigation.html navigation.js public-profile.html public-profile.js sites.css privacy.html"
CALENDAR_FILES="server/api.py server/beta_entry.py server/login_bridge.py server/publisher.py server/worker.py web/calendar.js web/calendar.html web/workspace.css"
backup() {
    target=$1
    mkdir -p "$BACKUP$(dirname "$target")"
    if [ -f "$target" ]; then
        cp -p "$target" "$BACKUP$target"
    else
        printf '%s\n' "$target" >> "$BACKUP/missing"
    fi
    printf '%s\n' "$target" >> "$BACKUP/manifest"
}
for file in $ACCOUNT_FILES; do
    [ -f "$SOURCE/account/$file" ] || exit 1
    backup "/opt/betasdc/$file"
done
for file in $ASSETS; do
    [ -f "$SOURCE/account/campus-register/$file" ] || exit 1
    backup "/opt/betasdc/static/$file"
done
for file in $CALENDAR_FILES; do
    [ -f "$SOURCE/calendar/$file" ] || exit 1
    backup "/opt/betasdc-calendar/$file"
done
backup /etc/betasdc/nginx.conf
backup /etc/betasdc/sites.nginx.conf
python3 -B -c '
import sqlite3,sys
from contextlib import closing
with closing(sqlite3.connect("/var/lib/betasdc/registrations.sqlite3")) as source:
    with closing(sqlite3.connect(sys.argv[1])) as target:
        source.backup(target)
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
        systemctl reload betasdc-nginx.service
        systemctl restart betasdc.service
        printf 'Deployment failed; restored code/config. Backup: %s\n' "$BACKUP" >&2
    fi
}
trap rollback EXIT
for file in $ACCOUNT_FILES; do install -o root -g root -m 644 "$SOURCE/account/$file" "/opt/betasdc/$file"; done
for file in $ASSETS; do install -o root -g root -m 644 "$SOURCE/account/campus-register/$file" "/opt/betasdc/static/$file"; done
for file in $CALENDAR_FILES; do install -o root -g root -m 644 "$SOURCE/calendar/$file" "/opt/betasdc-calendar/$file"; done
install -o root -g root -m 600 "$SOURCE/account/server/sites.nginx.conf" /etc/betasdc/sites.nginx.conf
python3 -B -c '
from pathlib import Path
path=Path("/etc/betasdc/nginx.conf")
source=path.read_text()
if "include /etc/betasdc/*.nginx.conf;" in source:
    source=source.replace("    include /etc/betasdc/calendar.nginx.conf;\n", "")
include="    include /etc/betasdc/sites.nginx.conf;"
if include not in source and "include /etc/betasdc/*.nginx.conf;" not in source:
    before,after=source.rsplit("}",1)
    if after.strip() or "upstream registration_backend" not in source:
        raise RuntimeError("Unexpected proxy layout")
    source=before+include+"\n}\n"
marker="        location ^~ /sso/ {"
if marker not in source:
    anchor="        location = /api/github/callback {"
    if source.count(anchor)!=1:
        raise RuntimeError("Unexpected account virtual host")
    block="""        location ^~ /sso/ {
            error_log /dev/null crit;
            if ($host = register.betasdc.duckdns.org) {
                return 302 https://account.betasdc.duckdns.org$request_uri;
            }
            proxy_pass http://registration_backend;
            proxy_http_version 1.1;
            proxy_set_header Host account.betasdc.duckdns.org;
            proxy_set_header X-Forwarded-For $remote_addr;
            proxy_set_header X-Forwarded-Proto https;
            proxy_set_header Connection "";
            proxy_connect_timeout 2s;
            proxy_read_timeout 15s;
            proxy_send_timeout 10s;
            proxy_buffering off;
        }
"""
    source=source.replace(anchor,block+anchor)
path.write_text(source)
'
/usr/sbin/nginx -e stderr -t -c /etc/betasdc/nginx.conf
cd /opt/betasdc-calendar
runuser -u betasdc -g www-data -- env CALENDAR_STATE=/var/lib/betasdc-calendar PYTHONPATH=/opt/betasdc \
    /opt/betasdc-calendar/.venv/bin/python -B -c \
    'import sys; sys.path.insert(0,"/opt/betasdc"); from calendar_service.beta_entry import create_app; create_app(); print("Site applications initialized")'
systemctl kill --kill-who=main --signal=HUP betasdc.service
sleep 10
systemctl reload betasdc-nginx.service
sleep 2
systemctl is-active --quiet betasdc.service betasdc-nginx.service
for host in account nav profile calendar; do
    printf 'Checking %s\n' "$host"
    curl --noproxy '*' --retry 5 --retry-all-errors --retry-delay 2 \
        --connect-timeout 3 --max-time 15 -fsS \
        --resolve "$host.betasdc.duckdns.org:443:10.16.125.52" \
        "https://$host.betasdc.duckdns.org/api/site/session" >/dev/null
done
printf 'Site upgrade complete. Backup: %s\n' "$BACKUP"
trap - EXIT
