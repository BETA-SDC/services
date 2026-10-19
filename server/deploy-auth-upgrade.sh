#!/bin/sh
# Scoped upgrade of the existing Linux deployment; credentials never leave the host.
set -eu
umask 077
test "$(id -u)" -eq 0
test "$(hostname -s)" = 550c
test "$(uname -s)" = Linux
SOURCE=/home/jet/betasdc-signup-github-stage
SNAPSHOT=/var/backups/betasdc/account-profile-$(date +%Y%m%d-%H%M%S)
test -s /etc/betasdc/github-client-id
test -s /etc/betasdc/github-client-secret
test -f "$SOURCE/server/registration/signup.py"
install -d -m 700 "$SNAPSHOT"
cp -a /opt/betasdc/server "$SNAPSHOT/server"
cp -a /opt/betasdc/static "$SNAPSHOT/static"
cp -a /etc/betasdc/nginx.conf "$SNAPSHOT/nginx.conf"
cp -a /etc/systemd/system/betasdc.service "$SNAPSHOT/betasdc.service"
if [ -f /etc/systemd/system/betasdc-github.service ]; then
    cp -a /etc/systemd/system/betasdc-github.service "$SNAPSHOT/betasdc-github.service"
fi
python3 - "$SNAPSHOT" <<'PY'
from contextlib import closing
from pathlib import Path
import re
import sqlite3
import sys

with closing(sqlite3.connect('/var/lib/betasdc/registrations.sqlite3')) as source:
    with closing(sqlite3.connect(str(Path(sys.argv[1]) / 'database.sqlite3'))) as target:
        source.backup(target)
        assert target.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
client = Path('/etc/betasdc/github-client-id').read_text().strip()
secret = Path('/etc/betasdc/github-client-secret').read_text().strip()
assert re.fullmatch(r'[A-Za-z0-9_.-]{10,100}', client)
assert re.fullmatch(r'[A-Za-z0-9_]{20,200}', secret)
for name in ('github-client-id', 'github-client-secret'):
    info = Path('/etc/betasdc', name).stat()
    assert info.st_uid == 0 and info.st_mode & 0o777 == 0o600
Path('/etc/betasdc/github.env').write_text('BETASDC_GITHUB_CLIENT_ID=' + client + '\n')
Path('/etc/betasdc/github.env').chmod(0o600)
PY
if ! getent passwd betasdc-github >/dev/null; then
    useradd --system --user-group --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin betasdc-github
fi
rollback() {
    result=$?
    if [ "$result" -eq 0 ]; then return; fi
    trap - EXIT
    set +e
    systemctl stop betasdc.service betasdc-github.service
    cp -a "$SNAPSHOT/server/." /opt/betasdc/server/
    cp -a "$SNAPSHOT/static/." /opt/betasdc/static/
    cp -a "$SNAPSHOT/nginx.conf" /etc/betasdc/nginx.conf
    cp -a "$SNAPSHOT/betasdc.service" /etc/systemd/system/betasdc.service
    if [ -f "$SNAPSHOT/betasdc-github.service" ]; then
        cp -a "$SNAPSHOT/betasdc-github.service" /etc/systemd/system/
    else
        systemctl disable betasdc-github.service
    fi
    systemctl daemon-reload
    systemctl start betasdc.service betasdc-mail.timer
    if [ -f "$SNAPSHOT/betasdc-github.service" ]; then systemctl start betasdc-github.service; fi
    systemctl reload betasdc-nginx.service
    printf 'Upgrade failed; previous application restored. Snapshot: %s\n' "$SNAPSHOT" >&2
    exit "$result"
}
trap rollback EXIT
systemctl stop betasdc-mail.timer betasdc-mail.service
systemctl stop betasdc.service
for module in "$SOURCE"/server/*.py; do
    install -o root -g root -m 644 "$module" /opt/betasdc/server/
done
for module in "$SOURCE"/server/registration/*.py; do
    install -o root -g root -m 644 "$module" /opt/betasdc/server/registration/
done
for asset in index.html styles.css main.js admin.html admin.js admin.css default-avatar.png; do
    install -o root -g root -m 644 "$SOURCE/campus-register/$asset" /opt/betasdc/static/
done
install -o root -g root -m 600 "$SOURCE/server/nginx.conf" /etc/betasdc/nginx.conf
install -o root -g root -m 644 "$SOURCE/server/betasdc.service" \
    "$SOURCE/server/betasdc-github.service" /etc/systemd/system/
systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/betasdc.service /etc/systemd/system/betasdc-github.service
/usr/sbin/nginx -t -c /etc/betasdc/nginx.conf
systemctl enable --now betasdc-github.service
systemctl restart betasdc-github.service
systemctl start betasdc.service
systemctl reload betasdc-nginx.service
(cd /opt/betasdc && runuser -u betasdc -g www-data -- python3 -B -m server.sync_github_profiles)
systemctl start betasdc-mail.timer
systemctl start betasdc-backup.service
printf 'Upgrade snapshot: %s\n' "$SNAPSHOT"
systemctl is-active betasdc.service betasdc-github.service betasdc-nginx.service betasdc-mail.timer
trap - EXIT
