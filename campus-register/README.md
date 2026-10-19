# Campus Registration Demo

Current fallback URL: `https://register.betasdc.duckdns.org:8443`

Standard URL after the administrator setup:
`https://register.betasdc.duckdns.org`

This is a username/email registration test, not a login or verified account
system. Use fictitious addresses such as `demo@example.com`. HTTPS protects
data in transit, but this demo is not ready to collect real personal information.

## Current Installation

- The original Python TLS fallback still binds to `10.17.131.26:8443`.
- The new proxy-mode backend only listens on `127.0.0.1:18080`.
- Nginx is installed and its loopback HTTPS proxy passed live tests, but its
  campus-interface listener timed out during TLS handshakes. It is not explicitly
  authorized in macOS Firewall. The 8443 Nginx LaunchAgent is disabled, and the
  original fallback was restored. Administrator installation is still required.
- Uses a browser-trusted Let's Encrypt certificate; TLS 1.2 or newer is required.
- The previous plaintext port 8080 is closed.
- Allows client addresses in the currently connected campus subnet
  `10.17.128.0/18`. Other campus subnets are not automatically trusted.
- Does not enable Tailscale Funnel, Serve, or router port forwarding.
- Uses parameterized SQLite writes, duplicate checks, bounded request bodies,
  a basic per-source rate limit, and same-origin/Host checks.
- Has no public registration-list endpoint and does not log submitted emails.
- Runtime and database are stored in
  `~/Library/Application Support/BetasdcDemo/`, with a private directory and
  database permissions. These permissions are not encryption.
- The loopback backend is a login LaunchAgent
  `org.betasdc.registration-backend`. Nginx 8443 and reload LaunchAgent templates
  are prepared but not active. The fallback is now a login LaunchAgent
  `org.betasdc.registration-demo`.
- Both Python services run through `sandbox-exec` with deployed
  `registration.sb`. The policy blocks direct content access to DuckDNS/ACME
  credentials, hidden home directories, common personal folders, keychains,
  browser/mail containers and source trees. It blocks changing deployed code,
  policy, certificates and Nginx files, and blocks active outbound connections.
- This is a targeted restriction with default allow, not complete account
  isolation. Unlisted paths and operating-system services are not comprehensively
  restricted. macOS `sandbox-exec` is a legacy, platform-specific mitigation;
  migrate to an independently provisioned service account/container on a server.
- Python caps accepted active requests at 32 total and 4 per socket-peer IP,
  before allocating a request thread or performing TLS handshakes. Excess
  connections are closed. The queue holds at most 32 pending connections.
  In proxy mode all upstream connections share the loopback peer cap; Nginx
  additionally enforces per-client limits when installed.
- Duplicate Host/Origin/length/type/forwarding headers, Transfer-Encoding and
  cross-site browser requests are rejected; deeply nested JSON returns 400.
- The prepared Nginx configuration enforces the campus subnet, fixed Host, 4 KiB request body limit,
  connection/request limits and timeouts. Forwarded client headers are replaced,
  not appended. The backend independently checks the forwarded campus address.
- The existing DuckDNS task still updates the campus IPv4 every five minutes.
  This demo does not automatically rebind if the Mac's local address changes.

## Stop

```sh
launchctl bootout "gui/$(id -u)/org.betasdc.nginx-8443"
launchctl bootout "gui/$(id -u)/org.betasdc.registration-backend"
launchctl bootout "gui/$(id -u)/org.betasdc.nginx-reload"
launchctl remove org.betasdc.registration-demo
```

Stopping does not delete registrations or disable the separate DuckDNS task.
It also does not disable certificate renewal. These LaunchAgents may load at
next login; move their plist files out of `~/Library/LaunchAgents/` to disable
permanently.

## Standard Port Setup (Pending)

The administrator setup is prepared but has not been run. In a local terminal:

```sh
sudo /bin/sh /Users/jietexue/_projects/betasdc/campus-register/enable-nginx-443.sh
```

Enter the Mac administrator password only in the terminal, never in chat.
This installs root-owned Nginx binaries, private libraries, fixed configuration
and private certificate copies in `/Library/BetasdcNginx/`.
The Nginx master runs as root to bind 443; its request-handling worker runs as
the built-in `_www` account. The backend remains the ordinary user's loopback
service. Root-owned LaunchDaemons are `org.betasdc.nginx-443` and
`org.betasdc.nginx-certificates`.

The installer checks HTTPS and the worker's user ID before stopping Nginx on
8443. On failure it removes its new installation and keeps 8443 available.
It refuses existing installations. The full privileged installation/rollback
has not been exercised because administrator credentials are unavailable here.

### Low-Port Security

The web application refuses root or mismatched real/effective user/group IDs
before opening the database. Python runs in
isolated mode (`-I`), ignoring Python environment overrides and user-site imports.
No setuid executable, passwordless sudo rule, global low-port relaxation,
router forwarding or PF rules are installed.
The administrator installer adds only the root-owned Nginx executable to the
macOS application firewall allow-list; it does not disable the firewall.
On failure it removes that new rule.

Root must not execute Nginx or load libraries directly from user-writable
Homebrew paths. `build-nginx-bundle.py` prepares a relocatable bundle as an
ordinary user; the administrator installer freezes a root-owned copy.
Non-system dynamic-library references are checked before execution.
Configuration and certificate-sync code are checked against pinned SHA-256
values, and service manifests are generated with fixed paths. No configuration
includes or dynamic Nginx modules are enabled. The root service does not open
user-controlled logs. Use `launchctl print` for service status.

Homebrew upgrades do not automatically update the root-owned Nginx or bundled
TLS libraries. Rebuild and reinstall during an administrator maintenance window;
the installer intentionally refuses overwriting an existing installation.
The backend currently requires this user's login to start, even when Nginx 443
is installed as a system service. Before login, Nginx can return 502. A real server
deployment should run its backend under a dedicated service account at boot.

This reduces administrator-privilege exposure. The web process still runs under
your personal Mac account with a targeted sandbox policy, not full isolation.
A compromised process can access files and services outside the policy's
restrictions, including its active TLS key and registration database.
Before handling real user data, migrate to a
dedicated unprivileged service account with a maintained production web server.
Campus-only binding does not override campus routing or client-isolation rules.

The old direct-Python `enable-standard-https.sh` installer is retired and refuses
installation. Its `--check` mode remains only for legacy validation tests.
Do not install the old `org.betasdc.registration-https.plist`.
Only the registration hostname is configured. Additional Nginx virtual hosts
require revising the fixed policy and pinned hashes.

After installation, verify from another campus device. To stop the standard
service (administrator permission required):

```sh
sudo launchctl bootout system/org.betasdc.nginx-443
sudo launchctl bootout system/org.betasdc.nginx-certificates
```

The installed plist remains and may load after reboot. To disable permanently,
move it out of `/Library/LaunchDaemons/`. Boot-time startup requires the configured
campus interface address to be available. If the campus address changes,
the Nginx binding and fixed installer policy need updating before reloading.

## Certificates

The certificate was issued using DuckDNS DNS-01 validation. No public inbound
web port was required. The certificate's domain is recorded in public
Certificate Transparency logs; the private key is not published.

Two independently renewed certificates now cover the full planned namespace:

- Apex: `betasdc.duckdns.org`.
- Wildcard: `*.betasdc.duckdns.org`, including `register`, `app`, and `api`.

The wildcard covers one subdomain level, not the apex or deeper names such as
`a.b.betasdc.duckdns.org`. The separate apex certificate covers the apex.
The fallback and prepared Nginx frontend use the wildcard certificate. Other sites
still require their own application and routing configuration.
Only the registration service is currently active.

- ACME client and state: `~/Library/BetasdcACME/`.
- Wildcard certificate state: `~/Library/BetasdcACME/certificates-wildcard/`.
- Apex certificate state: `~/Library/BetasdcACME/certificates-apex/`.
- Deployed wildcard certificate/key (used by registration):
  `~/Library/Application Support/BetasdcDemo/register-tls/`.
- Deployed apex certificate/key (reserved for a future apex site):
  `~/Library/Application Support/BetasdcDemo/tls/`.
- Renewal job: `~/Library/LaunchAgents/org.betasdc.certificate-renewal.plist`.
- Renewal check: once daily and at login, while the Mac is awake and online.
- Wildcard certificate expiry: 2027-01-11 22:07:14 Asia/Shanghai.
- Apex certificate expiry: 2027-01-11 22:08:43 Asia/Shanghai.
- Nginx 8443 checks for changed certificate files hourly and reloads only after
  a successful configuration test. On 443, the root-owned hourly sync service
  validates key matching, hostname, remaining validity (at least one day), and
  trust chain before copying certificates privately and testing/reloading Nginx.
  Neither reload job holds the DuckDNS token. ACME renewal still runs as the user.
- `renew-certificate.sh` is the portable source for the deployed renewal script.
- A copy of the DuckDNS credential is saved privately by the ACME client for
  renewal. Protect both this state directory and the original token file.
- Port 8443 avoids requiring administrator access for standard port 443.
- Previous individual certificates are retained on disk but removed from
  automatic renewal. Only the two new certificate profiles are renewed.

Renewal runs sequentially because DuckDNS uses one shared TXT verification
value for the apex and all subdomains. Each certificate has independent state
and automatically deploys to its saved destination. The wrapper treats "not
yet due" as success, attempts the other certificate even after a failure, and
reports failure if any real renewal error or missing configuration occurs.
There is no need to force renewal every day.

Clients must use the domain, not the numeric IP, for hostname verification.
Do not bypass certificate warnings if a connection fails.
To disable renewal:

```sh
launchctl bootout "gui/$(id -u)/org.betasdc.certificate-renewal"
```

The renewal plist remains installed; move it out of `~/Library/LaunchAgents/`
to disable it permanently. Long periods asleep/offline can prevent renewal.

## Run or Migrate

Python 3.9 or newer; no third-party dependencies required.

```sh
python3 app.py --bind SERVER_CAMPUS_IP --port 8443 \
  --database /private/path/registrations.sqlite3 \
  --allow CAMPUS_SUBNET_CIDR \
  --cert /private/path/fullchain.pem --key /private/path/key.pem
```

Replace the placeholders with actual values. Repeat `--allow` only for
verified campus subnets. The process account must have write permission
to the database directory; use mode 700 for that directory.
After moving to a server, move the DuckDNS updater too and verify DNS points
to that server's campus IPv4. Transfer ACME state and private keys securely
and configure server-side renewal. Do not keep two certificate-renewal jobs
running for the same domain.

The protected fallback is installed as a LaunchAgent. To reload it after a
verified deployment:

```sh
launchctl kickstart -k "gui/$(id -u)/org.betasdc.registration-demo"
```

The legacy command below bypasses `registration.sb`. Use only for
troubleshooting, not normal operation. Do not run while another service owns 8443:

```sh
launchctl submit -l org.betasdc.registration-demo \
  -o "$HOME/Library/Application Support/BetasdcDemo/server.log" \
  -e "$HOME/Library/Application Support/BetasdcDemo/server.error.log" \
  -- /usr/bin/python3 -I "$HOME/Library/Application Support/BetasdcDemo/app.py" \
  --bind 10.17.131.26 --port 8443 \
  --database "$HOME/Library/Application Support/BetasdcDemo/registrations.sqlite3" \
  --allow 10.17.128.0/18 \
  --cert "$HOME/Library/Application Support/BetasdcDemo/register-tls/fullchain.pem" \
  --key "$HOME/Library/Application Support/BetasdcDemo/register-tls/key.pem"
```

## Verification and Limitations

```sh
python3 -m unittest -v
```

Unit tests cover persistent writes, duplicate prevention, invalid data,
access checks, request size, rate limits, missing routes, and static assets.
TLS tests cover trusted encrypted registration, origin checks and certificate
reload. TLS unit tests generate isolated self-signed fixtures only; the deployed
server uses the real publicly trusted certificate.
An on-Mac request through the domain reached the health endpoint.
Desktop and mobile browser rendering passed visual checks. Browser submission
stored the fictitious record `browser_demo` / `browser_demo@example.com`;
that test record remains in the demo database. Mobile rendering had no
horizontal overflow, and the bitmap brand asset loaded correctly.
After the HTTPS upgrade, all eight tests passed without resource warnings.
A normal curl request with certificate verification succeeded, and a browser
confirmed `window.isSecureContext === true`. An HTTPS browser registration
created `https_demo` / `https_demo@example.com`; that test record also remains
in the database. Port 8080 no longer accepts connections. The daily renewal
job ran successfully and correctly skipped the newly issued certificate.
After adding the registration subdomain, all ten tests passed, including the
standard-port Host/Origin rules and inherited listening socket handling.
A real launchd socket-activation test on port 9443 served verified HTTPS;
the temporary test service was then removed. The standard port 443 installation
remains pending local administrator authorization.
The subdomain's 8443 fallback also passed browser registration, secure-context
and desktop/mobile layout checks. The fictitious record
`register_demo` / `register_demo@example.com` remains in the demo database.
The installer was checked for shell/plist syntax and rejected a non-root
invocation without installing anything. Its privileged installation and
rollback paths cannot be fully verified until administrator authorization.
After provisioning wildcard/apex certificates, all fifteen tests passed.
The five renewal-wrapper tests simulate successful renewal, skipped renewal,
failures and missing configuration without contacting the certificate authority.
A verified HTTPS health request received the deployed wildcard certificate.
Hostname checks passed for `app.betasdc.duckdns.org` and the apex using the
appropriate certificates; a deeper name was correctly rejected.
Access from another campus device remains unverified.

Network isolation or the Mac firewall may still block other devices.
Clients using DNS rebinding protection may reject private-address DNS records.
Do not weaken such protections globally to work around a failed test.

Only the connected subnet is allowed. This is not proof of campus identity:
a VPN or routed network could also reach an allowed address range.
This Python standard-library server is a small test service, not a hardened
production deployment. Before collecting real data, add appropriate
authentication, email verification, privacy controls,
operational limits and backup/retention policies.

This deliverable is outside a Git repository; no commit or push was created.
