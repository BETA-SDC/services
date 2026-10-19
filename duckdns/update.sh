#!/bin/sh
set -eu
umask 077

DOMAIN=betasdc
PLATFORM=$(uname -s)
case "$PLATFORM" in
    Linux)
        export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
        TOKEN_FILE=${DUCKDNS_TOKEN_FILE:-/var/lib/betasdc-acme/duckdns.token}
        EXPECTED_GATEWAY=${DUCKDNS_EXPECTED_GATEWAY:-10.16.127.254}
        ADDRESS_PREFIX=${DUCKDNS_ADDRESS_PREFIX:-10.16.}
        ;;
    Darwin)
        TOKEN_FILE=${DUCKDNS_TOKEN_FILE:-"$HOME/Library/Application Support/DuckDNS/betasdc.token"}
        EXPECTED_GATEWAY=${DUCKDNS_EXPECTED_GATEWAY:-10.17.191.254}
        ADDRESS_PREFIX=${DUCKDNS_ADDRESS_PREFIX:-10.17.}
        ;;
    *)
        printf '%s\n' "ERROR: unsupported operating system" >&2
        exit 1
        ;;
esac

log() {
    printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*"
}

case "$PLATFORM" in
    Darwin)
        route_info=$(/sbin/route -n get default 2>/dev/null)
        gateway=$(printf '%s\n' "$route_info" |
            awk '$1 == "gateway:" { print $2; exit }')
        interface=$(printf '%s\n' "$route_info" |
            awk '$1 == "interface:" { print $2; exit }')
        ;;
    Linux)
        route_info=$(ip -4 route show default)
        gateway=$(printf '%s\n' "$route_info" |
            awk '{ for (i=1; i<NF; i++) if ($i == "via") { print $(i+1); exit } }')
        interface=$(printf '%s\n' "$route_info" |
            awk '{ for (i=1; i<NF; i++) if ($i == "dev") { print $(i+1); exit } }')
        ;;
    *)
        log "ERROR: unsupported operating system"
        exit 1
        ;;
esac

if [ "$gateway" != "$EXPECTED_GATEWAY" ]; then
    log "SKIP: default gateway does not match the configured network"
    exit 0
fi

case "$PLATFORM" in
    Darwin)
        local_ip=$(/usr/sbin/ipconfig getifaddr "$interface" 2>/dev/null)
        ;;
    Linux)
        local_ip=$(ip -4 -o addr show dev "$interface" scope global |
            awk '{ split($4, address, "/"); print address[1]; exit }')
        ;;
esac

# Only publish a private address from the expected campus address range.
if ! printf '%s\n' "$local_ip" | awk -F. '
    NF != 4 { exit 1 }
    {
        for (i = 1; i <= 4; i++)
            if ($i !~ /^[0-9]+$/ || $i > 255) exit 1
        if ($1 == 10 ||
            ($1 == 172 && $2 >= 16 && $2 <= 31) ||
            ($1 == 192 && $2 == 168)) exit 0
        exit 1
    }'; then
    log "SKIP: interface address is not a valid private IPv4"
    exit 0
fi
case "$local_ip" in
    "$ADDRESS_PREFIX"*) ;;
    *)
        log "SKIP: interface address does not match the campus address prefix"
        exit 0
        ;;
esac

if [ ! -r "$TOKEN_FILE" ]; then
    log "ERROR: token file is not readable"
    exit 1
fi

token=$(cat "$TOKEN_FILE")
case "$token" in
    ''|*[!a-fA-F0-9-]*)
        log "ERROR: invalid token format"
        exit 1
        ;;
esac
if [ "${#token}" -ne 36 ]; then
    log "ERROR: invalid token length"
    exit 1
fi

# Feed the credential through stdin, not command-line arguments.
if ! response=$(
    printf 'url = "https://www.duckdns.org/update?domains=%s&token=%s&ip=%s&verbose=true"\n' \
        "$DOMAIN" "$token" "$local_ip" |
        /usr/bin/curl -q --config - --ipv4 --noproxy '*' \
            --proto '=https' --fail --silent --show-error \
            --connect-timeout 10 --max-time 30 2>/dev/null
); then
    log "ERROR: HTTPS update failed; existing DNS records were not intentionally cleared"
    exit 1
fi
unset token

status=$(printf '%s\n' "$response" | tr -d '\r' | sed -n '1p')
if [ "$status" != OK ]; then
    log "ERROR: DuckDNS rejected the update"
    exit 1
fi
ipv4=$(printf '%s\n' "$response" | tr -d '\r' | sed -n '2p')
if [ "$ipv4" != "$local_ip" ]; then
    log "ERROR: DuckDNS did not confirm the requested campus address"
    exit 1
fi
change=$(printf '%s\n' "$response" | tr -d '\r' | sed -n '4p')
case "$change" in
    UPDATED|NOCHANGE) ;;
    *)
        log "ERROR: unexpected DuckDNS response"
        exit 1
        ;;
esac
log "OK: $DOMAIN.duckdns.org IPv4=$ipv4 $change"
