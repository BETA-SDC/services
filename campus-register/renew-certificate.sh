#!/bin/sh
set -eu
umask 077
case "$(uname -s)" in
    Linux)
        export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
        ACME_HOME=${BETASDC_ACME_HOME:-/var/lib/betasdc-acme}
        ;;
    Darwin)
        export PATH=/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin
        ACME_HOME=${BETASDC_ACME_HOME:-"$HOME/Library/BetasdcACME"}
        ;;
    *)
        printf '%s\n' "ERROR: unsupported operating system" >&2
        exit 1
        ;;
esac

renew_one() {
    certificate_home=$1
    domain=$2
    if [ ! -r "$certificate_home/${domain}_ecc/${domain}.conf" ]; then
        printf 'ERROR: Missing certificate configuration for %s\n' "$domain" >&2
        return 1
    fi
    if /bin/sh "$ACME_HOME/acme.sh" --renew --ecc \
        --home "$ACME_HOME" --cert-home "$certificate_home" \
        --server letsencrypt -d "$domain"; then
        return 0
    else
        result=$?
        # acme.sh returns 2 when a valid certificate is not yet due.
        [ "$result" -eq 2 ] || return "$result"
    fi
}

# DuckDNS has one shared TXT value; never renew these in parallel.
status=0
renew_one "$ACME_HOME/certificates-wildcard" '*.betasdc.duckdns.org' || status=1
renew_one "$ACME_HOME/certificates-apex" 'betasdc.duckdns.org' || status=1
exit "$status"
