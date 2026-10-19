#!/bin/sh
# Full account release. No Nginx, listener or firewall changes.
set -eu
exec /bin/sh "$(dirname "$0")/deploy-checkin-governance.sh" "$@"
