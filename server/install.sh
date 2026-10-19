#!/bin/sh
# One-time server installation; DNS cutover and timer activation are separate.
set -eu
umask 077
STAGE=/home/jet/betasdc-deploy-20261014
SOURCE="$STAGE/source"
test "$(id -u)" -eq 0
if [ "$(uname -s)" != Linux ]; then
    printf '%s\n' "This installer requires Linux with systemd."
    exit 1
fi
for path in /opt/betasdc /etc/betasdc /var/lib/betasdc /var/lib/betasdc-acme; do
    if [ -e "$path" ] || [ -L "$path" ]; then
        printf 'Refusing existing deployment path: %s\n' "$path"
        exit 1
    fi
done
systemctl disable nginx.service
if getent passwd betasdc >/dev/null || getent passwd betasdc-acme >/dev/null; then
    printf '%s\n' "Refusing to reuse existing service accounts"
    exit 1
fi
useradd --system --user-group --home-dir /var/lib/betasdc --shell /usr/sbin/nologin betasdc
useradd --system --user-group --home-dir /var/lib/betasdc-acme --shell /usr/sbin/nologin betasdc-acme
install -d -o root -g root -m 755 /opt/betasdc /opt/betasdc/server \
    /opt/betasdc/server/registration /opt/betasdc/static /usr/local/libexec
for name in index.html styles.css main.js settings.js private.js privacy.html admin.html admin.js admin.css icons.js LICENSE.lucide.txt brand-logo.png brand-logo-display.png beta-favicon-v2.ico default-avatar.png beta-shell.js beta-shell.css navigation.html navigation.js public-profile.html public-profile.js sites.css; do
    install -o root -g root -m 644 "$SOURCE/campus-register/$name" "/opt/betasdc/static/$name"
done
install -o root -g root -m 644 "$SOURCE/server/__init__.py" /opt/betasdc/server/__init__.py
install -o root -g root -m 644 "$SOURCE/server/requirements-private.txt" /opt/betasdc/server/
python3 -m pip install --only-binary=:all: --target /opt/betasdc/server/_vendor \
    -r /opt/betasdc/server/requirements-private.txt
for module in "$SOURCE"/server/registration/*.py; do
    install -o root -g root -m 644 "$module" /opt/betasdc/server/registration/
done
install -o root -g root -m 644 "$SOURCE/server/webapp.py" /opt/betasdc/server/webapp.py
install -o root -g root -m 644 "$SOURCE/server/test_webapp.py" /opt/betasdc/server/test_webapp.py
install -o root -g root -m 644 "$SOURCE/server/test_registration.py" /opt/betasdc/server/test_registration.py
install -o root -g root -m 644 "$SOURCE/server/backup.py" "$SOURCE/server/test_backup.py" /opt/betasdc/server/
install -o root -g root -m 644 "$SOURCE/server/test_auth.py" /opt/betasdc/server/test_auth.py
install -o root -g root -m 644 "$SOURCE/server/test_private.py" /opt/betasdc/server/
install -o root -g root -m 644 "$SOURCE/server/mail_worker.py" "$SOURCE/server/test_verification.py" /opt/betasdc/server/
install -o root -g root -m 644 "$SOURCE/server/github_broker.py" "$SOURCE/server/test_github.py" \
    "$SOURCE/server/test_signup.py" "$SOURCE/server/test_support.py" /opt/betasdc/server/
install -o root -g root -m 644 "$SOURCE/server/github_profile.py" "$SOURCE/server/github_organization.py" \
    "$SOURCE/server/manage_admin.py" "$SOURCE/server/test_permissions.py" "$SOURCE/server/test_profile.py" /opt/betasdc/server/
install -o root -g root -m 644 "$SOURCE/server/sync_github_profiles.py" \
    "$SOURCE/server/test_github_profile.py" /opt/betasdc/server/
install -o root -g root -m 755 "$SOURCE/campus-register/renew-certificate.sh" /opt/betasdc/renew-certificate.sh
install -o root -g root -m 755 "$SOURCE/duckdns/update.sh" /opt/betasdc/update-duckdns.sh
install -o root -g root -m 755 "$SOURCE/server/sync-certificates.py" /usr/local/libexec/betasdc-sync-certificates.py
install -o root -g root -m 755 "$SOURCE/server/refresh-network.py" /usr/local/libexec/betasdc-refresh-network.py
install -o root -g root -m 755 "$SOURCE/server/backup.py" /usr/local/libexec/betasdc-backup.py
install -d -o root -g root -m 700 /var/backups/betasdc /var/backups/betasdc/database
install -d -o betasdc -g www-data -m 700 /var/lib/betasdc
install -o betasdc -g www-data -m 600 "$STAGE/betasdc-database.sqlite3" /var/lib/betasdc/registrations.sqlite3
install -d -o betasdc-acme -g betasdc-acme -m 700 /var/lib/betasdc-acme
tar --no-same-owner -xzf "$STAGE/betasdc-acme-state.tar.gz" -C /var/lib/betasdc-acme
install -o betasdc-acme -g betasdc-acme -m 600 "$STAGE/betasdc.token" /var/lib/betasdc-acme/duckdns.token
chown -R betasdc-acme:betasdc-acme /var/lib/betasdc-acme
find /var/lib/betasdc-acme -type d -exec chmod 700 {} +
find /var/lib/betasdc-acme -type f -exec chmod 600 {} +
install -d -o betasdc-acme -g betasdc-acme -m 700 \
    /var/lib/betasdc-acme/deployed /var/lib/betasdc-acme/deployed/apex /var/lib/betasdc-acme/deployed/wildcard
runuser -u betasdc-acme -- /bin/sh /var/lib/betasdc-acme/acme.sh --install-cert --ecc \
    --home /var/lib/betasdc-acme --cert-home /var/lib/betasdc-acme/certificates-apex \
    -d betasdc.duckdns.org --key-file /var/lib/betasdc-acme/deployed/apex/key.pem \
    --fullchain-file /var/lib/betasdc-acme/deployed/apex/fullchain.pem
runuser -u betasdc-acme -- /bin/sh /var/lib/betasdc-acme/acme.sh --install-cert --ecc \
    --home /var/lib/betasdc-acme --cert-home /var/lib/betasdc-acme/certificates-wildcard \
    -d '*.betasdc.duckdns.org' --key-file /var/lib/betasdc-acme/deployed/wildcard/key.pem \
    --fullchain-file /var/lib/betasdc-acme/deployed/wildcard/fullchain.pem
find /var/lib/betasdc-acme/deployed -type f -exec chmod 600 {} +
install -d -o root -g root -m 700 /etc/betasdc /etc/betasdc/tls /etc/betasdc/wildcard
install -o root -g root -m 600 "$SOURCE/server/nginx.conf" /etc/betasdc/nginx.conf
install -d -o www-data -g www-data -m 700 /var/cache/betasdc-nginx
install -d -o root -g root -m 755 /run/betasdc-nginx
for unit in "$SOURCE"/server/betasdc*.service "$SOURCE"/server/betasdc*.timer; do
    install -o root -g root -m 644 "$unit" /etc/systemd/system/
done
systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/betasdc*.service /etc/systemd/system/betasdc*.timer
python3 -I /usr/local/libexec/betasdc-sync-certificates.py
cd /opt/betasdc
python3 -W error::ResourceWarning -m unittest discover -s server -p 'test_*.py' -v
systemctl enable --now betasdc.service betasdc-nginx.service
systemctl start betasdc-backup.service
systemctl enable --now betasdc-backup.timer
printf '%s\n' "Installed services; validate HTTPS before DNS cutover."
