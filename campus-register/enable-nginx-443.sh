#!/bin/sh
set -eu
umask 077
PATH=/usr/bin:/bin:/usr/sbin:/sbin
export PATH
SOURCE="/Users/jietexue/Library/Application Support/BetasdcDemo/nginx"
BASE=/Library/BetasdcNginx
OPENSSL_CONF=/dev/null
OPENSSL_MODULES="$BASE/lib"
export OPENSSL_CONF OPENSSL_MODULES
DAEMON=/Library/LaunchDaemons/org.betasdc.nginx-443.plist
RENEW=/Library/LaunchDaemons/org.betasdc.nginx-certificates.plist
installed=no
success=no
firewall_added=no
[ "$(id -u)" -eq 0 ] || { printf '%s\n' "Run once with sudo in your terminal."; exit 1; }
for path in "$BASE" "$DAEMON" "$RENEW"; do
    if [ -e "$path" ] || [ -L "$path" ]; then
        printf '%s\n' "Refusing to overwrite an existing installation: $path"
        exit 1
    fi
done
if /bin/launchctl print system/org.betasdc.registration-https >/dev/null 2>&1; then
    printf '%s\n' "The old direct-Python 443 service is installed; stop it before continuing."
    exit 1
fi
[ "$(/usr/sbin/ipconfig getifaddr en0)" = 10.17.131.26 ]
uid=$(/usr/bin/id -u jietexue)
[ "$uid" -ne 0 ]
cleanup() {
    if [ "$installed" = yes ] && [ "$success" = no ]; then
        /bin/launchctl bootout system/org.betasdc.nginx-certificates 2>/dev/null || :
        /bin/launchctl bootout system/org.betasdc.nginx-443 2>/dev/null || :
        /bin/rm -f "$DAEMON" "$RENEW"
        if [ "$firewall_added" = yes ]; then
            /usr/libexec/ApplicationFirewall/socketfilterfw --remove "$BASE/bin/nginx" || :
        fi
        /bin/rm -rf "$BASE"
        printf '%s\n' "Rolled back; the 8443 Nginx fallback remains available."
    fi
}
trap cleanup EXIT
/bin/mkdir -m 755 "$BASE"
installed=yes
/bin/mkdir -m 755 "$BASE/bin" "$BASE/lib"
/bin/mkdir -m 700 "$BASE/tls"
/bin/mkdir -m 700 "$BASE/temp"
/usr/sbin/chown _www:_www "$BASE/temp"
/usr/bin/install -o root -g wheel -m 755 "$SOURCE/bundle/bin/nginx" "$BASE/bin/nginx"
for name in libcrypto.4.dylib libpcre2-8.0.dylib libssl.4.dylib; do
    file="$SOURCE/bundle/lib/$name"
    [ -f "$file" ] && [ ! -L "$file" ]
    /usr/bin/install -o root -g wheel -m 755 "$file" "$BASE/lib/"
done
/usr/bin/install -o root -g wheel -m 600 "$SOURCE/nginx-443.conf" "$BASE/nginx.conf"
/usr/bin/install -o root -g wheel -m 700 \
    "$SOURCE/sync-nginx-certificates.sh" "$BASE/sync-certificates.sh"
check_hash() {
    actual=$(/usr/bin/shasum -a 256 "$1" | /usr/bin/awk '{print $1}')
    [ "$actual" = "$2" ] || { printf '%s\n' "Rejected modified privileged configuration."; exit 1; }
}
check_hash "$BASE/nginx.conf" b23a0ed00f00ad8ffe12752e30ce6fcfa4c056b88fdfb78af5f5b72060c2880c
check_hash "$BASE/sync-certificates.sh" 0fb0ae1dbead93ae6a4c67f98598e1985140d8ee0248897ba4e092a2b27016d2
check_hash "$BASE/bin/nginx" a326111a7cad498416e1eb1422e561840800f6730197da646d8a3d897ca7f322
check_hash "$BASE/lib/libcrypto.4.dylib" b8770167303f58a8dbaf268ad11c73f87ce39f6f6cb150dae86923f02002a75e
check_hash "$BASE/lib/libpcre2-8.0.dylib" 6bdada4b73872c137a39d7cb224627e7b7a1a39f718307e22e0365846e90a0c4
check_hash "$BASE/lib/libssl.4.dylib" daa1c927e5e58c0879caa18f1ad91738101471d375bc187bb5cfd780a4b6fb7d
# Root uses only the frozen config/binaries, never mutable Homebrew dependencies.
# Verify relocation before executing the copied binary.
for file in "$BASE/bin/nginx" "$BASE"/lib/*.dylib; do
    /usr/bin/codesign --verify --strict "$file"
    /usr/bin/otool -L "$file" | /usr/bin/awk '
        NR > 1 && $1 !~ /^\/usr\/lib\// && $1 !~ /^\/System\/Library\// &&
        $1 !~ /^@executable_path\/\.\.\/lib\/[A-Za-z0-9_.+-]+\.dylib$/ &&
        $1 !~ /^@loader_path\/[A-Za-z0-9_.+-]+\.dylib$/ { bad=1 }
        END { exit bad }'
done
/usr/bin/install -o root -g wheel -m 600 \
    "/Users/jietexue/Library/Application Support/BetasdcDemo/register-tls/fullchain.pem" \
    "$BASE/tls/fullchain.pem"
/usr/bin/install -o root -g wheel -m 600 \
    "/Users/jietexue/Library/Application Support/BetasdcDemo/register-tls/key.pem" \
    "$BASE/tls/key.pem"
"$BASE/bin/nginx" -e stderr -t -p "$BASE/" -c "$BASE/nginx.conf"
/usr/libexec/ApplicationFirewall/socketfilterfw --add "$BASE/bin/nginx"
firewall_added=yes
/usr/libexec/ApplicationFirewall/socketfilterfw --unblockapp "$BASE/bin/nginx"
# Generate service manifests with fixed trusted paths, not caller-supplied plists.
/usr/bin/python3 -I - <<'PY'
import plistlib

for path, config in [
    ("/Library/LaunchDaemons/org.betasdc.nginx-443.plist", {
        "Label": "org.betasdc.nginx-443",
        "ProgramArguments": [
            "/Library/BetasdcNginx/bin/nginx", "-e", "stderr",
            "-p", "/Library/BetasdcNginx/",
            "-c", "/Library/BetasdcNginx/nginx.conf",
        ],
        "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 10,
        "EnvironmentVariables": {
            "OPENSSL_CONF": "/dev/null",
            "OPENSSL_MODULES": "/Library/BetasdcNginx/lib",
        },
    }),
    ("/Library/LaunchDaemons/org.betasdc.nginx-certificates.plist", {
        "Label": "org.betasdc.nginx-certificates",
        "ProgramArguments": ["/bin/sh", "/Library/BetasdcNginx/sync-certificates.sh"],
        "StartInterval": 3600, "RunAtLoad": True,
    }),
]:
    with open(path, "xb") as target:
        plistlib.dump(config, target)
PY
/bin/launchctl bootstrap system "$DAEMON"
attempt=0
while [ "$attempt" -lt 10 ]; do
    if /usr/bin/curl -q --noproxy '*' --fail --silent \
        --resolve register.betasdc.duckdns.org:443:10.17.131.26 --max-time 5 \
        https://register.betasdc.duckdns.org/api/health >/dev/null; then
        break
    fi
    attempt=$((attempt + 1))
    /bin/sleep 1
done
[ "$attempt" -lt 10 ]
pid=$(/bin/launchctl print system/org.betasdc.nginx-443 | \
    /usr/bin/awk '$1 == "pid" && $2 == "=" {print $3}')
case "$pid" in ''|*[!0-9]*) exit 1 ;; esac
worker_uid=$(/usr/bin/id -u _www)
workers=$(/bin/ps -axo ppid=,uid=,command= | \
    /usr/bin/awk -v parent="$pid" '$1 == parent && /nginx: worker process/ {print $2}')
[ "$workers" = "$worker_uid" ]
/bin/launchctl bootstrap system "$RENEW"
success=yes
/bin/launchctl bootout "gui/$uid/org.betasdc.nginx-8443" 2>/dev/null || :
/bin/launchctl bootout "gui/$uid/org.betasdc.nginx-reload" 2>/dev/null || :
/bin/launchctl bootout "gui/$uid/org.betasdc.registration-demo" 2>/dev/null || :
/bin/launchctl disable "gui/$uid/org.betasdc.nginx-8443" || :
/bin/launchctl disable "gui/$uid/org.betasdc.nginx-reload" || :
printf '%s\n' "Ready: https://register.betasdc.duckdns.org"
