#!/usr/bin/python3
"""Rebind only to the verified campus interface when DHCP changes its IPv4."""
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


def refresh():
    routes = json.loads(subprocess.check_output(
        ["/usr/sbin/ip", "-j", "-4", "route", "show", "default"], text=True
    ))
    if not routes or routes[0].get("gateway") != "10.16.127.254":
        print("Network refresh skipped: unexpected campus gateway")
        return
    interface = routes[0]["dev"]
    addresses = json.loads(subprocess.check_output(
        ["/usr/sbin/ip", "-j", "-4", "address", "show", "dev", interface], text=True
    ))
    candidates = [info["local"] for item in addresses for info in item["addr_info"]
                  if info.get("scope") == "global"]
    if not candidates or ipaddress.ip_address(candidates[0]) not in ipaddress.ip_network("10.16.112.0/20"):
        raise ValueError("Unexpected server campus subnet")
    address = candidates[0]
    path = Path("/etc/betasdc/nginx.conf")
    previous = path.read_text()
    replacement, count = re.subn(
        r"(?m)^        listen [0-9.]+:443 ssl default_server;$",
        "        listen " + address + ":443 ssl default_server;",
        previous,
    )
    if count != 1:
        raise ValueError("Unexpected Nginx listen configuration")
    if replacement == previous:
        print("Campus binding unchanged")
        return
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stage:
        stage.write(replacement)
        temporary = Path(stage.name)
    temporary.chmod(0o600)
    os.replace(temporary, path)
    try:
        subprocess.run(["/usr/sbin/nginx", "-e", "stderr", "-t",
                        "-c", str(path)], check=True)
        subprocess.run(["/usr/bin/systemctl", "reload", "betasdc-nginx.service"], check=True)
    except Exception:
        path.write_text(previous)
        path.chmod(0o600)
        raise
    print("Campus HTTPS binding updated:", address)


if __name__ == "__main__":
    refresh()
