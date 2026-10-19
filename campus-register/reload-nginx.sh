#!/bin/sh
set -eu
umask 077
PATH=/usr/bin:/bin:/usr/sbin:/sbin
export PATH
BASE="/Users/jietexue/Library/Application Support/BetasdcDemo/nginx"
NGINX=/opt/homebrew/bin/nginx
TLS="/Users/jietexue/Library/Application Support/BetasdcDemo/register-tls"
test -r "$BASE/nginx.pid" || exit 0
signature=$(/usr/bin/shasum -a 256 "$TLS/fullchain.pem" "$TLS/key.pem")
previous=
if [ -r "$BASE/certificate-signature" ]; then
    previous=$(/bin/cat "$BASE/certificate-signature")
fi
[ "$signature" != "$previous" ] || exit 0
"$NGINX" -e stderr -t -c "$BASE/nginx.conf" -p "$BASE/"
"$NGINX" -e stderr -s reload -c "$BASE/nginx.conf" -p "$BASE/"
printf '%s\n' "$signature" > "$BASE/certificate-signature"
