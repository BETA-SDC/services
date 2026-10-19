#!/bin/sh
# Remove legacy source-IP lists without changing any listener or other service.
set -eu
umask 077
[ "$(id -u)" = 0 ] && [ "$(hostname)" = 550c ] || exit 1
SOURCE=${1:?Staged account source}
CALENDAR=${2:?Staged calendar source}
PYTHON=/opt/betasdc-calendar/.venv/bin/python
BACKUP=$(mktemp -d /var/backups/betasdc/access-upgrade-XXXXXX)
backup() {
    mkdir -p "$BACKUP$(dirname "$1")"
    if [ -f "$1" ]; then cp -p "$1" "$BACKUP$1"
    else printf '%s\n' "$1" >> "$BACKUP/missing"; fi
    printf '%s\n' "$1" >> "$BACKUP/manifest"
}
for file in server/registration/config.py server/test_webapp.py server/test_checkin.py server/test_network_policy.py server/nginx.conf server/sites.nginx.conf server/deploy-access-upgrade.sh README.md OVERVIEW.md server/README.md; do
    [ -f "$SOURCE/$file" ]
    backup "/opt/betasdc/$file"
done
for file in beta_entry.py login_bridge.py; do
    [ -f "$CALENDAR/server/$file" ]
    backup "/opt/betasdc-calendar/server/$file"
done
for file in /etc/betasdc/nginx.conf /etc/betasdc/sites.nginx.conf; do backup "$file"; done
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
        systemctl reload betasdc-nginx.service
        printf 'Access upgrade rolled back. Backup: %s\n' "$BACKUP" >&2
    fi
}
trap rollback EXIT
for file in server/registration/config.py server/test_webapp.py server/test_checkin.py server/test_network_policy.py server/nginx.conf server/sites.nginx.conf server/deploy-access-upgrade.sh README.md OVERVIEW.md server/README.md; do
    install -o root -g root -m 644 "$SOURCE/$file" "/opt/betasdc/$file"
done
for file in beta_entry.py login_bridge.py; do
    install -o root -g root -m 644 "$CALENDAR/server/$file" "/opt/betasdc-calendar/server/$file"
done
"$PYTHON" -B - "$SOURCE/server/nginx.conf" <<'PY'
from pathlib import Path
import re
import sys

for name, indent in (("nginx.conf", "        "), ("sites.nginx.conf", "    ")):
    path = Path("/etc/betasdc") / name
    original = path.read_text()
    old = "".join(indent + line + "\n" for line in (
        "allow 10.16.112.0/20;", "allow 10.17.0.0/18;", "allow 10.17.128.0/18;", "deny all;"))
    if old in original:
        assert original.count(old) == 1
        updated = original.replace(old, indent + "allow all;\n")
    else:
        assert indent + "allow all;\n" in original, "Unexpected access rules"
        updated = original
    if name == "nginx.conf" and "location ^~ /api/checkin/ {" not in updated:
        source = Path(sys.argv[1]).read_text()
        block = source.split("        location ^~ /api/checkin/ {", 1)[1].split("\n        location ", 1)[0]
        updated = updated.replace("        location ^~ /api/calendar/ {",
                                  "        location ^~ /api/checkin/ {" + block +
                                  "\n        location ^~ /api/calendar/ {", 1)
        assert updated.count("location ^~ /api/checkin/ {") == 1
    assert re.findall(r"^\s*listen .+;", original, re.M) == re.findall(r"^\s*listen .+;", updated, re.M)
    assert not re.search(r"^\s*(allow (?!all;)|deny )", updated, re.M)
    path.write_text(updated)
PY
/usr/sbin/nginx -e stderr -t -c /etc/betasdc/nginx.conf
cd /opt/betasdc
runuser -u betasdc -g www-data -- "$PYTHON" -B -m unittest server.test_network_policy server.test_webapp server.test_checkin server.test_sites
cd /opt/betasdc-calendar
runuser -u betasdc -g www-data -- env CALENDAR_STATE=/var/lib/betasdc-calendar "$PYTHON" -B -c '
import sys
sys.path.insert(0,"/opt/betasdc")
from calendar_service.beta_entry import create_app
app=create_app()
client=app.test_client()
for host in ("account", "calendar", "profile", "nav"):
    for address in ("10.17.64.1", "10.26.79.1", "203.0.113.1", "2001:db8::1"):
        response=client.get("/api/site/session",headers={"Host":host+".betasdc.duckdns.org","X-Forwarded-For":address,"X-Forwarded-Proto":"https"})
        assert response.status_code==200, (host,address,response.status_code)
        assert response.json["sso_available"]
with app.app_context():
    from server.registration.checkin import calendar_events
    print("Calendar selector can read",len(calendar_events()),"events")
print("All shared hosts and SSO accept formerly excluded networks")
'
systemctl kill --kill-who=main --signal=HUP betasdc.service
sleep 8
systemctl reload betasdc-nginx.service
systemctl is-active --quiet betasdc.service betasdc-nginx.service betasdc-calendar.service
for host in account calendar profile nav; do
    curl --noproxy '*' --connect-timeout 3 --max-time 15 -fsS \
        --resolve "$host.betasdc.duckdns.org:443:10.16.125.52" \
        "https://$host.betasdc.duckdns.org/api/site/session" |
        "$PYTHON" -c 'import json,sys; assert json.load(sys.stdin)["sso_available"]'
done
printf 'Access upgrade deployed; listeners unchanged. Backup: %s\n' "$BACKUP"
trap - EXIT
