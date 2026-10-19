# betasdc DuckDNS

## Linux Server

The active deployment is on Linux server `550c.duckdns.org`. The updater is
`/opt/betasdc/update-duckdns.sh`, its private token is
`/var/lib/betasdc-acme/duckdns.token`, and `betasdc-dns.timer` runs every five
minutes. Logs are available with `sudo journalctl -u betasdc-dns.service`.
The parent record `betasdc.duckdns.org` and the registration hostname
`register.betasdc.duckdns.org` resolve to campus IPv4 `10.16.125.52`.

The Mac configuration below is a disabled backup. Do not start its updater
alongside the Linux server.

## Installed on this Mac

- Domain: `betasdc.duckdns.org`
- Mode: campus private IPv4, not public egress IPv4.
- Verified DNS A record on 2026-10-13: `10.17.131.26`; no AAAA record.
- Local IPv4 at setup: `10.17.131.26`
- Expected default gateway: `10.17.191.254`
- Expected private address prefix: `10.17.`
- Agent: `~/Library/LaunchAgents/org.duckdns.betasdc.plist`
- Schedule: every 300 seconds, starting at login; asleep/offline Macs cannot update.
- Token: `~/Library/Application Support/DuckDNS/betasdc.token` (mode 600).
- Logs: `~/Library/Application Support/DuckDNS/betasdc.log`
  and `betasdc.error.log`.

The agent runs a deployed copy at
`~/Library/Application Support/DuckDNS/update.sh`, avoiding macOS restrictions
on background access to Documents. This directory contains the portable source
and documentation, and can be moved without affecting the installed agent.
A separate HTTPS registration demo is now installed at
`https://register.betasdc.duckdns.org:8443`; see `../campus-register/README.md`.
No public port forwarding or firewall exception has been configured.
DNS success is not proof of incoming connectivity.

The updater uses HTTPS and explicitly publishes the private IPv4 of the
default-route interface, rather than asking DuckDNS to detect a public IPv4.
Credentials are not included in process arguments or logs.
The gateway check prevents most accidental updates after moving networks,
but is only a coarse guard: other networks can reuse the same gateway address.
The address must also be RFC1918 private IPv4 and match the configured prefix.
IPv6 records are left untouched.

## Campus access

The demo fallback URL is `https://register.betasdc.duckdns.org:8443`.
It uses a publicly trusted certificate. Setup for
`https://register.betasdc.duckdns.org` on port 443 is prepared, but still
requires the administrator setup command in the registration README.
The app must bind to the campus interface, not just `127.0.0.1`.

Publishing private IPv4 is not a campus-only access control policy. The DNS
record is publicly readable, but the address is not globally routable.
Campus routing, client isolation and DNS rebinding protection can prevent
access even from campus clients. VPN users may also have routes to this address.
The demo checks client addresses against `10.17.128.0/18`. A test from another
campus device is still needed to confirm access. Tailscale Serve/Funnel is
not enabled.

## Check or stop

```sh
launchctl print "gui/$(id -u)/org.duckdns.betasdc"
tail -n 10 "$HOME/Library/Application Support/DuckDNS/betasdc.log"
sh "$HOME/Library/Application Support/DuckDNS/update.sh"
```

Stop it before activating an updater on the server:

```sh
launchctl bootout "gui/$(id -u)/org.duckdns.betasdc"
```

The plist remains installed and will load at the next login. For a permanent
migration, move that plist out of `~/Library/LaunchAgents/` as well.
Keep the token private; do not commit it or share it in messages.

## Move to a server

1. Stop and permanently disable the Mac agent.
2. Transfer `update.sh` and the token securely to the server.
3. Place the token in a private directory, with directory mode 700 and file
   mode 600, owned by the account that runs the updater.
4. Check the server's default gateway; set `DUCKDNS_EXPECTED_GATEWAY`
   accordingly. Set `DUCKDNS_ADDRESS_PREFIX` for its campus subnet, and
   `DUCKDNS_TOKEN_FILE` to the token's new absolute path.
5. Run once and verify `OK`, then schedule it every five minutes.

The script supports macOS and Linux (Linux requires `ip`, `curl`, and
standard shell utilities). For example, after installing the script and
token in `/opt/duckdns/`, a Linux crontab entry for the owning account is:

```cron
*/5 * * * * DUCKDNS_TOKEN_FILE=/opt/duckdns/betasdc.token DUCKDNS_EXPECTED_GATEWAY=10.17.191.254 /bin/sh /opt/duckdns/update.sh >> /opt/duckdns/update.log 2>&1
```

Substitute the actual server paths, gateway and address prefix.
The default prefix is `10.17.`; other campus subnets need an override.
Set up log rotation for
long-term use. Do not run both the Mac and server updaters concurrently.

After migration, the DNS record must change to the server's private IPv4.
Campus clients still need network routing to that address and service port;
no public router port forwarding is needed for direct campus access.

## Verification

At installation, shell syntax and plist validation passed.
Wrong-gateway and missing-token branches were tested.
The campus-mode updater returned `OK / UPDATED` for `10.17.131.26`.
DNS lookup returned the same address, and the reloaded launch agent returned
`OK / NOCHANGE` with exit code 0. Wrong-gateway and wrong-prefix tests skipped
without sending update requests.
This directory and the installed configuration are outside a Git repository.
