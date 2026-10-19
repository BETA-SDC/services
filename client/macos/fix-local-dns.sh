#!/bin/sh
set -eu
umask 077
test "$(uname -s)" = Darwin
test "$(id -u)" -eq 0
source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
target=/etc/resolver/betasdc.duckdns.org
if [ -e "$target" ] || [ -L "$target" ]; then
    printf '%s\n' "Resolver already exists; refusing to replace existing settings." >&2
    exit 1
fi
/usr/bin/install -d -o root -g wheel -m 755 /etc/resolver
/usr/bin/install -o root -g wheel -m 644 "$source_dir/betasdc.resolver" "$target"
rollback() {
    /bin/rm "$target"
    /usr/bin/dscacheutil -flushcache
    /usr/bin/killall -HUP mDNSResponder
}
trap rollback EXIT
/usr/bin/dscacheutil -flushcache
/usr/bin/killall -HUP mDNSResponder
attempt=0
while [ "$attempt" -lt 6 ]; do
    if /usr/bin/curl --noproxy '*' --connect-timeout 5 --max-time 8 \
        --silent --show-error --fail \
        https://register.betasdc.duckdns.org/api/health; then
        trap - EXIT
        printf '\n%s\n' "Domain-specific DNS repair verified."
        exit 0
    fi
    attempt=$((attempt + 1))
    /bin/sleep 1
done
printf '%s\n' "DNS repair did not restore access; reverting the added resolver." >&2
exit 1
