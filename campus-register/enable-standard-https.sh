#!/bin/sh
set -eu
umask 077
PATH=/usr/bin:/bin:/usr/sbin:/sbin
export PATH

validate_configuration() {
    /usr/bin/python3 -I - "$1" <<'PY'
import plistlib
import pwd
import sys

runtime = "/Users/jietexue/Library/Application Support/BetasdcDemo"
expected = {
    "Label": "org.betasdc.registration-https",
    "UserName": "jietexue",
    "GroupName": "staff",
    "ProgramArguments": [
        "/usr/bin/python3", "-I", runtime + "/app.py",
        "--bind", "10.17.131.26", "--port", "443",
        "--launchd-socket", "CampusHTTPS",
        "--database", runtime + "/registrations.sqlite3",
        "--allow", "10.17.128.0/18",
        "--cert", runtime + "/register-tls/fullchain.pem",
        "--key", runtime + "/register-tls/key.pem",
    ],
    "Sockets": {"CampusHTTPS": {
        "SockFamily": "IPv4", "SockType": "stream",
        "SockNodeName": "10.17.131.26", "SockServiceName": "443",
        "SockPassive": True,
    }},
    "RunAtLoad": True, "KeepAlive": {"SuccessfulExit": False},
    "ThrottleInterval": 10,
}
with open(sys.argv[1], "rb") as source:
    actual = plistlib.load(source)
if actual != expected or pwd.getpwnam("jietexue").pw_uid == 0:
    sys.exit("Rejected service configuration: identity, arguments or socket policy changed.")
print("Service configuration matches the unprivileged campus-only policy.")
PY
}

if [ "${1:-}" = --check ] && [ "$#" -eq 2 ]; then
    validate_configuration "$2"
    exit 0
fi
if [ "$#" -ne 0 ]; then
    printf '%s\n' "Usage: enable-standard-https.sh [--check PLIST]"
    exit 1
fi
printf '%s\n' "The direct-Python 443 installer is retired. Use enable-nginx-443.sh instead."
exit 1
