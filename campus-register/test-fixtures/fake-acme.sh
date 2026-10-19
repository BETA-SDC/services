#!/bin/sh
set -eu
certificate_home=
domain=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --cert-home) certificate_home=$2; shift ;;
        -d) domain=$2; shift ;;
    esac
    shift
done
case "$certificate_home" in
    */certificates-wildcard)
        printf 'wildcard %s\n' "$domain" >> "$RENEW_TEST_LOG"
        exit "${WILDCARD_RESULT:-2}"
        ;;
    */certificates-apex)
        printf 'apex %s\n' "$domain" >> "$RENEW_TEST_LOG"
        exit "${APEX_RESULT:-2}"
        ;;
    *) exit 99 ;;
esac
