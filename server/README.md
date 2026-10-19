# Server Deployment

Deployed on 2026-10-14 to Ubuntu host `550c.duckdns.org`, campus IPv4
`10.16.125.52`. URL: `https://account.betasdc.duckdns.org`.

The account center moved from `register.betasdc.duckdns.org` on 2026-10-15.
The old hostname redirects to the same path on `account`; existing email
verification fragments are preserved by browser redirects. Host-only cookies
are not shared, so users need to log in again. Account data is not reset.

Only Betasdc binds campus IPv4 port 443. Existing services, including the
service on port 80, SSH port 22, and global firewall rules were preserved.
The default Nginx unit is disabled; the dedicated `betasdc-nginx` unit uses
its own configuration.

Registration uses the `register` hostname and the existing wildcard
certificate. The apex hostname is no longer a registration endpoint.
DuckDNS still updates the parent `betasdc` record; its nested hostnames resolve
to the same campus address.

The shared DNS and renewal scripts select Linux defaults under Linux:
`/var/lib/betasdc-acme`, system executable paths, and Linux `ip` commands.
The older Mac path defaults are used only when running on macOS.

## Files And Services

- Application package: `/opt/betasdc/server/registration`, owned by root.
- Frontend assets: `/opt/betasdc/static`, owned by root.
- HTTPS configuration and deployed certificates: `/etc/betasdc`, root-only.
- Registration database: `/var/lib/betasdc/registrations.sqlite3`, mode 600.
- ACME state and DuckDNS token: `/var/lib/betasdc-acme`, separate service user.
- Backend: `betasdc.service`, unprivileged Gunicorn on a protected Unix socket.
- HTTPS: `betasdc-nginx.service`.
- Email delivery: `betasdc-mail.timer`, checks the outbox about every 30 seconds;
  `betasdc-mail.service` connects to 163 SMTP over verified TLS.
- GitHub binding: `betasdc-github.service`, a separate unprivileged broker
  with a private Unix socket and outbound HTTPS access.
- DNS: `betasdc-dns.timer`, every five minutes.
- Renewal: `betasdc-renew.timer`, daily with randomized delay.
- Certificate deployment: `betasdc-certificates.timer`, hourly.
- Database backup: `betasdc-backup.timer`, daily at 03:30 Asia/Shanghai with
  up to 15 minutes of randomized delay; retains the latest 14 verified backups
  under `/var/backups/betasdc/database` (directory 700, files 600, root-only).

As of 2026-10-16 no client subnet allowlist is used: Nginx, account,
profile, calendar and SSO accept any IPv4/IPv6 source able to route to
the campus listener. This does not make the private server reachable from
the public Internet. Earlier deployment notes describing subnet denial
are historical, not current policy. Registration accepts a name, email and password,
and permits at most 1,000 records. New accounts require an email code before
the registration row is created. Password recovery is not implemented.
New registrations require an exact `westlake.edu.cn` email domain; subdomains
and similar-looking suffixes are rejected. The form shows the fixed suffix
and accepts either the local part or a pasted full address in that domain.
Domain restriction alone does not verify email ownership. Existing records
are preserved, and login does not impose a new email-domain restriction.

The existing apex and wildcard certificates were migrated and verified.
They expire on 2027-01-11. Renewal is configured, but direct and proxy
connections from the server to the Let's Encrypt API timed out during
deployment. Future renewal is not yet verified and requires working outbound
HTTPS connectivity; current HTTPS is working.

## SSH

The current local alias `ssh beta` uses user `jet` and dedicated Ed25519 key
`/Users/jietexue/.ssh/id_ed25519_betasdc_550c`. Key-only authentication was
verified independently. Password authentication and sudo policy were not
changed. Termius import is left to the user.

## Validation

The application test suite covers password hashing,
CSRF, revocable sessions, expiration, persistent login throttling, malformed
login JSON, legacy-account reservations, configuration isolation,
input normalization, existing data preservation, duplicate rollback,
concurrent record limits, generic storage errors, HTTP access policy, and
database backup integrity/permissions/retention.
Verification tests also cover one-use tokens, expiration, resend cooldown,
concurrent requests, transaction rollback, safe cross-site email navigation,
delivery retries, and redacted SMTP failure logs.
Signup tests cover no account before ownership confirmation, code expiry,
guess exhaustion, session/email binding, cooldown, delivery, and concurrent
single-use consumption. GitHub tests cover state/session binding, PKCE,
expiry, denial, provider errors, ID uniqueness, unchanged existing bindings,
recent authentication, and logout during exchange.
The previous 43-test suite also passed under the deployed backend account.
The account-center/profile upgrade has 49 passing tests, including public
snapshot persistence, organization pagination, ID-preserving refresh, concurrent
unlink, field filtering, and legacy binding migration.
Live browser checks exercised code registration, email login, logout,
the GitHub authorization redirect, and safe handling of an invalid provider
code. Final authorization with a real GitHub account remains a user action.
Live HTTPS checks passed for the page,
stylesheet, JavaScript, image, health endpoint, registration, blocked foreign
origin, and oversized requests. The database was copied again after stopping
the Mac application to retain existing records.
Five renewal-wrapper tests also passed on Linux after the hostname migration.
The live register hostname accepted a test registration with its own Origin
and rejected the former apex Origin. These wrapper tests use a fake ACME
client; they do not establish outbound connectivity to Let's Encrypt.

The new authentication pages were exercised against live HTTPS with
Playwright: registration, confirmation mismatch, password visibility,
incorrect password, login, reload persistence, and logout. Desktop 1280px,
mobile 390px, and compact 320px widths had no horizontal overflow; local
brand images and icons loaded. The synthetic account was removed afterward,
leaving the original eight records, and a fresh database backup was verified.

The previous Mac registration services, DNS updater, and certificate renewal
LaunchAgents were disabled. Do not enable them alongside the server.

## Client DNS Repair

On 2026-10-14 the Mac's ordinary UDP DNS queries returned the former address
`10.17.131.26`, while HTTPS DNS and TCP DNS returned server `10.16.125.52`.
This locates the discrepancy in the ordinary DNS resolution path but does
not identify which resolver or network device retained the old answer.

The Mac now has `/etc/resolver/betasdc.duckdns.org` pointing at `1.1.1.1`
with `options usevc`. It applies only to this domain, uses TCP DNS, and was
verified with a normal HTTPS request and browser registration. It creates no
background job and does not hardcode the server IP. The helper source is
`client/macos/` in the project. Ordinary `dig` still queries the primary DNS
server and does not test macOS's domain-specific resolver selection.

Other clients with the same DNS discrepancy may need their own resolver
repair. Public DNS and the server were verified; availability across every
campus network has not been established.

## Application Structure

`server/webapp.py` preserves the Gunicorn entry point. The `registration/`
package contains:

- `__init__.py`: application factory and feature registration.
- `config.py`: database path, hostname, campus networks, assets, and limits.
- `security.py`: shared request checks, response headers, and HTTP errors.
- `routes.py`: page routes and the registration API blueprint.
- `auth.py`: password authentication, session signing, login, and logout.
- `verification.py`: one-use verification and queued delivery requests.
- `signup.py`: registration-time email ownership challenges.
- `github.py`: session-bound, optional GitHub account binding.
- `validation.py`: input rules and normalized registration data.
- `repository.py`: SQLite schema and transactional storage operations.

The original registration rows and API URL are preserved. Storage connections
are scoped to operations; the count and insert remain in one serialized
transaction. Initialization migrates the old schema transactionally by adding
a nullable `password_hash` column and the `auth_sessions` and `login_attempts`
tables. The eight pre-upgrade records have no password and cannot log in.
Schema version 2 also adds nullable `email_verified_at`, `email_verifications`,
and `mail_outbox`. Password-bearing accounts migrated from the previous
version must verify before login; existing sessions are not accepted until
the associated email is verified. Migration never silently verifies users
or sends mail to all existing records.
Their usernames and emails remain reserved; there is no public account-claim
endpoint. Do not assign a password based solely on knowing an old email.
Schema version 3 adds `signup_challenges`, `signup_mail_outbox`,
`github_accounts`, and `github_flows`; existing accounts and legacy verification
links are preserved.
Schema version 4 adds `github_accounts.profile_json` and `synced_at`,
preserving existing bindings and backfilling public data through the broker.

Add a feature with a Flask blueprint registered in the application factory.
Keep HTTP concerns in its routes, input rules in validation, and SQL in the
repository. Shared access checks apply to every registered blueprint.
Schema changes require an explicit migration; `CREATE TABLE IF NOT EXISTS`
does not upgrade existing tables.

## Authentication

Pages are `/register`, `/login`, `/account`, and `/verify`, on the same HTTPS hostname.
The root opens registration, or the current account if already authenticated.

1. `GET /api/session` returns a CSRF token and the current user, or `null`.
2. Send that token as `X-CSRF-Token` on every POST, with the same cookies.
3. `POST /api/signup/code`: JSON `email`; queues a six-digit code without
   creating an account. Then `POST /api/register`: JSON `username`, `email`,
   `password`, `email_code`; success is 201 with `verification_required=false`.
   Account creation and consuming the challenge occur in one transaction.
   Registering does not log in automatically.
   The email must end in the exact domain `@westlake.edu.cn`; the backend
   validates this independently of the frontend.
4. `POST /api/login`: JSON `identifier` (username or email), `password`;
   success returns the user and a rotated CSRF token.
5. `POST /api/logout`: revoke the server session; returns a new CSRF token.
6. `POST /api/verification/resend`: JSON `email`; generic success response
   for unknown, verified, and pending email addresses.
7. `POST /api/verification/confirm`: JSON `token`; marks the email verified
   atomically, consumes the token, and removes obsolete sessions/outbox jobs.

A correct password for a pending account returns HTTP 409 with code
`email_verification_required` and displays the resend page. It does not
create a session. Incorrect passwords still use the generic HTTP 401 error.
This pending-account path is retained for accounts created before version 3.

Passwords require 15-128 characters and use Werkzeug's salted
PBKDF2-SHA256 with 600,000 iterations. Passwords and hashes never appear in
API responses. Unknown accounts and incorrect passwords return the same
login error. Application limits survive restarts: each identifier allows ten
attempts and each client address thirty attempts per fifteen minutes,
separately for login and registration. Nginx also limits these POST routes.

Sessions expire after twelve hours and are revoked on logout. The
`__Host-betasdc` cookie is Secure, HttpOnly, SameSite=Lax, and host-only.
SQLite stores only SHA-256 hashes of random session tokens.
`/var/lib/betasdc/registrations.session-key` is generated once by the service,
must remain owned by `betasdc` with mode 600, and is not source code.
Preserve it privately if migrating active sessions; deliberately replacing
it logs everyone out. Database backups contain password hashes and session
metadata and must remain private. The daily database backup does not include
this separate signing key.

## Email Verification Delivery

New registrations use six-digit codes valid for ten minutes, with five guesses
per challenge and a sixty-second resend cooldown across browser sessions.
Challenges are bound to the requesting browser session and exact email.
The database stores a keyed HMAC of each code, using the private session key.
The delivery outbox temporarily holds the plaintext code until accepted by
SMTP; expired jobs are removed. Successful registration consumes both the
challenge and any queued job. No account is created on an incorrect or expired
code. Registration remains restricted to the exact school email domain.
Persistent IP/email limits and Nginx limits apply to code requests.
The user has confirmed actual inbox delivery from the configured 163 sender.

Sender: `BETA SDC Service <betasdc@163.com>`. SMTP: `smtp.163.com:465`, certificate
verification enabled, credential is the 163 client authorization code.
The root-only source file `/etc/betasdc/smtp-password` must have mode 600.
Systemd `LoadCredential` passes it privately to the mail worker. It is never
copied into source code, environment variables, command arguments, logs, or
the SQLite database. The web backend retains `RestrictAddressFamilies=AF_UNIX`
and does not perform SMTP or read the mail credential.

The separate oneshot worker runs as the unprivileged `betasdc` account.
It reads at most ten queued messages per run, uses a process lock, deletes
accepted jobs, and retries temporary failures with exponential delays up to
eight attempts within the token lifetime. Journal messages contain only
counts and exception class names. SMTP acceptance does not prove delivery
to the recipient inbox; users should also check junk mail.

For legacy pending accounts, verification tokens contain 32 random bytes, expire 30 minutes after the
request, and can be used once. Only a SHA-256 digest is in the verification
table. The durable outbox temporarily holds the plaintext token needed to
construct the email, so both the database and its backups remain sensitive.
The worker removes expired jobs and tokens. A resend is allowed after 60
seconds and replaces the prior token; clients must use the newest email.
The existing application attempt limits and Nginx limits also cover these
routes. Old password-less records cannot use verification to claim accounts.

The email URL carries the token in its fragment (`/verify#token=...`), which
is not sent in the initial HTTP request. The UI removes it from the address
bar and submits it only when the user presses the confirmation button, with
CSRF protection. GET does not verify an email. A narrow exception permits
cross-site top-level GET navigation to `/verify`; cross-site API requests
remain blocked. Campus network access remains required to open the link.

To rotate a credential, replace the root-only file without exposing its
contents, then the next worker invocation loads the new credential. To disable
delivery, stop and disable `betasdc-mail.timer`. Inspect status with:

```sh
sudo systemctl status betasdc-mail.timer
sudo journalctl -u betasdc-mail.service --since '15 minutes ago'
```

Never paste the authorization code or raw verification tokens into support
logs. The daily database backup does not include the SMTP credential.
The pre-verification rollback snapshot is
`/var/backups/betasdc/verification-20261014`; reconcile new data before any restore.

## GitHub Binding

The registered OAuth App callback is intentionally retained as
`https://register.betasdc.duckdns.org/api/github/callback`.
That endpoint redirects to the new account hostname, where the current
host-only session and one-use state are checked. Both authorization and
token exchange continue using the registered URI; no GitHub settings change
is required for this migration. Do not change only one of these URLs.
Binding is optional; it does not replace the verified school email and password,
and does not enable GitHub login.
Users may select the registration checkbox to start binding after their first
login, or use the binding button on the account page.

`POST /api/github/link` requires CSRF and a password login within ten minutes.
It creates a single-use, ten-minute state bound to the current database
session and uses PKCE S256. The top-level GitHub callback GET is the only
additional cross-site exception; other cross-site API requests remain blocked.
The callback consumes state before exchanging the code and rechecks session
validity after the exchange. It stores the numeric GitHub ID, login, public
profile snapshot, public organizations, and synchronization time.
A GitHub ID can belong to only one local account. Existing bindings cannot
be silently replaced. No repository or private-email scopes are requested.

The root-only files `/etc/betasdc/github-client-id` and
`/etc/betasdc/github-client-secret` must have mode 600.
Only the broker receives the secret through systemd `LoadCredential`.
It runs as `betasdc-github`, has no database access, uses verified HTTPS
to fixed GitHub endpoints, refuses redirects, and returns public profile data over
`/run/betasdc-github/broker.sock` (mode 660, group www-data).
Authorization codes and access tokens are not persisted or logged.
The web backend retains `RestrictAddressFamilies=AF_UNIX`.
It receives the public client ID through the root-only
`/etc/betasdc/github.env` environment file. Without that configuration,
GitHub registration controls are hidden and binding is disabled.

The dedicated calendar configuration and systemd drop-in are retained.
Nginx loads root-owned `*.nginx.conf` integration files and preserves the
calendar API body limit. The campus ranges include the existing calendar
deployment's `10.17.0.0/18`; this is also the current client's observed subnet.

After proving identity through authenticated `GET /user`, the broker fetches
the full public `GET /user/{account_id}` response and paginated
`GET /users/{login}/orgs` responses. All returned public profile fields and
organization entries are retained in `github_accounts.profile_json`.
This does not recursively download repository contents, follower lists, or
event history. Public email fields, if published by the user, may be in the
private backend snapshot. The authenticated profile and OAuth tokens are never
persisted. No additional private-data scopes are requested. Public requests use
OAuth application HTTP Basic authentication (client ID/secret) only inside
the broker, avoiding the shared-IP anonymous quota without a user token.

The account page receives only display name, safe derived avatar/profile URLs,
public organization names/links, identity, and sync metadata; it does not
receive the full snapshot. GitHub avatars load directly from the restricted
`avatars.githubusercontent.com` host with no referrer; an icon is shown if
loading fails. Display fields use text content, never HTML.
An empty public organization list does not imply no memberships: private
memberships are unavailable without extra permissions. Pagination is bounded
by a ten-page/1000-entry and request-time budget; incomplete lists are marked.
Responses larger than 512 KiB are rejected rather than silently truncated.

`POST /api/github/refresh` requires login, CSRF, and persistent throttling.
It fetches public data without retaining a token or reauthorizing, verifies the
stable numeric ID, and checks the session and binding again before saving.
`python3 -B -m server.sync_github_profiles` backfills existing bindings via the
same private broker. Run as `betasdc` with group `www-data` from `/opt/betasdc`;
it prints only success/failure counts.

`POST /api/github/unlink` requires CSRF and a recent password login,
deletes pending flows, and rotates the current session to cancel callbacks
already in flight. Unlinking removes the local association; users can also
revoke the OAuth App in GitHub's authorized-app settings.

`server/deploy-auth-upgrade.sh` upgrades only the existing BETA deployment
and creates a root-only `account-profile-YYYYMMDD-HHMMSS` snapshot under
`/var/backups/betasdc`. It requires pre-provisioned credentials.
For a fresh installation, provision the broker account and credentials,
write `BETASDC_GITHUB_CLIENT_ID=<public client ID>` to the root-only
`/etc/betasdc/github.env`, and enable `betasdc-github.service` only after
those files exist. Do not put the client secret in the environment file.

The upgrade rollback snapshot is root-only at
`/var/backups/betasdc/auth-20261014`, with the previous source, Nginx
configuration, and database. Do not overwrite the live database with this
snapshot after accepting new registrations without reconciling their data.

`create_app(config={...})` accepts Flask configuration overrides. Supported
keys are `REGISTRATION_DOMAIN`, `REGISTRATION_DATABASE`,
`REGISTRATION_NETWORKS`, `REGISTRATION_ASSETS`, `REGISTRATION_MAX_RECORDS`,
and `MAX_CONTENT_LENGTH`. Explicit config overrides the compatibility
arguments `database`, `networks`, and `max_records`. Changing the hostname
or network ranges also requires updating Nginx's corresponding rules.
Pass a temporary database for development/tests; never point tests at the
production database.

Frontend source remains in `campus-register/index.html`, `styles.css`, and
`main.js`. Icons are self-hosted Lucide 0.468.0 in `icons.js`; attribution
is in `LICENSE.lucide.txt` and the project `THIRD-PARTY.md`.
The user-supplied BETA logo is stored as `brand-logo.png`; the page uses
`brand-logo-display.png`, which removes only transparent outer margins.
Both files must be included when deploying the frontend.
The browser favicon is `beta-favicon-v2.ico`, with six sizes from 16 to 256
pixels; its new URL avoids reusing the old `/mark.png` favicon cache.
The other Mac-specific files in that directory are legacy backups
and are not imported or installed by the Linux application.

Run application tests from the project root on Linux with Flask installed:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -W error::ResourceWarning \
  -m unittest discover -s server -p 'test_*.py' -v
/bin/sh -n server/install.sh
```

This source directory is outside a Git repository; no commit or push applies.

## Account Administration And GitHub Groups

`/admin` and `/api/admin/*` require a verified session and a server-checked
administrator role. Session cookies or browser-supplied role fields cannot
grant access. Mutations also require CSRF protection and password authentication
within the last ten minutes. Administrative changes are recorded in
`admin_audit`; password hashes, OAuth tokens and SMTP secrets are never returned
by the management endpoints.

There are two independent administrator sources:

- A manually assigned local role in `account_roles`.
- A BETA-SDC GitHub Owner verified during OAuth authorization, recorded in
  `github_admin_grants` with a six-calendar-month validity period. Ordinary organization
  members and team maintainers do not receive this role.

Binding/re-authorizing GitHub now requests `read:org`. Existing bindings must
use **授权并同步组织与 Team** once to grant it. GitHub organization policies may
require approval of the OAuth application by an organization owner.
The broker checks active organization memberships, the user's team memberships,
and the authorized account's visible BETA-SDC team hierarchy. The user token
exists only in broker memory during this request; no user access token is stored.
Consequently this is a bounded authorization snapshot, not continuous GitHub
synchronization: private team memberships expire after 24 hours, while Owner
administrator verification expires after six calendar months in Asia/Shanghai.
Month-end dates clamp to the last day of the target month. Owner verification
timestamps are retained in `github_owner_verifications`; existing active
24-hour Owner grants migrate from their original verification timestamp once.
Public-profile refresh does not renew Owner or team
privileges. GitHub-side role changes can remain effective locally until the
snapshot expires, unless an earlier successful authorization verifies the change.
Use a GitHub App/webhook integration in a later deployment if immediate
GitHub-side revocation or unattended private synchronization is required.

An owner-authorized complete team listing is authoritative for the whole
organization. Partial listings never delete teams belonging to other users'
visible snapshots. Parent/child relationships use stable GitHub numeric IDs;
membership in a child team also includes its known ancestor teams. Organization
owners are not automatically members of every team. Unknown permissions, API
errors and truncated responses are not interpreted as non-membership.
Only GitHub identity, organization role, team ID/name/slug/parent and the user's
team IDs are retained from private organization endpoints, not full private
organization or other users' profile data.

GitHub-managed groups are read-only in the UI. Local groups can be created,
renamed, nested, assigned and archived separately. Successful confirmation that
a user left BETA-SDC removes their GitHub memberships and Owner-derived role.
Unbinding GitHub revokes them immediately; expired grants are denied on every
request. Local roles and local groups remain independent.

For explicit host-local recovery only, a verified exact local username can
be made a manual administrator:

```sh
cd /opt/betasdc
runuser -u betasdc -g www-data -- python3 -B -m server.manage_admin \
  --username EXACT_VERIFIED_LOCAL_USERNAME
```

Do not bootstrap the first registrant automatically. Deployment must not alter
existing users' roles except through verified GitHub authorization or an
explicit administrator action.

## Names And Nicknames

Registration now requires `real_name` (up to 80 letters/marks/spaces and name
punctuation) and accepts an optional printable `nickname` (up to 40 characters).
These fields are not unique and are not authentication credentials or proof of
legal identity. New clients do not choose a username: the server generates an
opaque stable internal identifier. Existing usernames and email login continue
to work unchanged, so other services can retain their current identity keys.

Migration leaves legacy real names empty and preserves old usernames as
nicknames. It never guesses legal names. `/api/profile` allows a logged-in user
with recent password authentication and valid CSRF protection to edit their own
name/nickname only; it cannot change email, password, roles or GitHub binding.

Cross-site top-level GET navigation to `/account` is allowed so that OAuth's
clean return redirect can finish. This exception does not permit cross-site
API requests, iframe navigation or mutations.

The message center is planned only. No message database, popup, push mechanism
or scheduled notification task is installed by this upgrade.

## Optional Private Data

Schema version 9 adds session-bound second-factor proofs to optional student ID, mainland mobile number and
document information in `private_fields`. Only these optional fields use
AES-256-GCM encryption; ordinary names and email remain in their existing
tables. This is server-side encryption, not end-to-end encryption. The
service-owned 0600 `registrations.private-key` is separate from the database
and session signing key. Never regenerate it when ciphertext exists.

`GET /api/private` returns only the caller's saved-field flags, passkey count and
document options. Private reads/edits/deletes require explicit consent to
the current policy version, exact fields/purpose, a valid session, CSRF and
fresh email OTP or WebAuthn verification, reusable for at most five minutes in the
same revocable login session. Password alone and legacy cookie timestamps cannot
unlock private data. Explicit action buttons express each operation's consent;
privacy is a direct settings option at `/account#privacy`, with desktop sidebar
and mobile top navigation. The old `/account/privacy` route remains compatible.
The resulting hashed capability is
session-bound, expires in 60 seconds and is atomically consumed once. Mutations
revoke outstanding capabilities. There is no administrator or external-app
private-data read endpoint.

Yubico `fido2` verifies challenges, exact origin/RP, signatures, UP/UV and
supported counters. Device PIN is permitted; biometric templates are never
collected. Discoverable passkeys can also log in without entering email; a
browser-bound challenge maps the credential and signed user handle to a verified
account, then rotates the normal session. Login does not unlock private data.
Maximum five passkeys; enrollment and removal require password plus fresh second
factor. Passkeys and email OTP share the 2FA settings panel. Registration suggests
adding a passkey. Normal password login does not require a second factor.

Email OTP is bound to the current verified email and login session, expires in
five minutes, permits five guesses and has a 60-second account-wide send cooldown.
Only its keyed digest is used for verification; plaintext exists briefly in the
outbound mail queue, removed on delivery or expiry. The existing isolated mail
worker sends these messages without widening backend network permissions.
Phone edits accept only `1[3-9][0-9]{9}` without `+86`; historical encrypted +86
mainland numbers normalize on read.

`server/requirements-private.txt` pins dependencies installed in `server/_vendor`
without changing the shared calendar virtual environment. The scoped
`deploy-private-upgrade.sh` backs up code/database, installs dependencies,
initializes schema/key under the service identity, saves a root-only key recovery
copy under `/var/backups/betasdc/keys`, and gracefully reloads Gunicorn without
changing Nginx, ports or units. Both key copies remain on the same machine;
separate controlled offline backup is still required for disk-loss recovery.

`/privacy` describes optional collection, verification limitations, storage,
single-use consent, residual root-access risk and deletion/backup retention.
Audit holds no private values, is not displayed in settings and is pruned after 180 days on subsequent
private operations. Deleted ciphertext can remain in restricted backups until
rotation; recovery requires reconciling later deletion requests. Account deletion
requests go to the policy contact, not an unimplemented self-service endpoint.

## Shared Site Shell And SSO

Schema 10 adds public profile/address requests and separate site-session metadata,
without altering the existing auth-session columns. The shell is injected into
HTML by the account application and its calendar-host instances. The left menu
links directly to the calendar; the old `nav.betasdc.duckdns.org` root redirects
there for compatibility. Public profiles at
`profile.betasdc.duckdns.org/<registration-number-or-approved-slug>`. Both keep the
existing campus allowlist. The menu currently has only Calendar. The account
control shows only the avatar by default; opening it shows the avatar, nickname
and Sign out. Public APIs explicitly allowlist nickname/code, avatar,
bio and GitHub link, never names, emails, private fields, teams or admin status.
The default profile path is the immutable system registration number, independent
of real name and nickname. Address requests require an authenticated
CSRF-protected request; an active administrator approves or rejects them with an
audit entry. Aliases are permanent
and cannot be reassigned to another account. Bio content is rendered as text.

SSO issues a 60-second single-use ticket bound to exact destination host and a
nonce in that browser's signed host-only session. Return paths are relative,
validated and exclude API/SSO routes. Tickets and state are stored only as hashes.
Separate host signing keys and Secure/HttpOnly `__Host-` cookies are retained;
there is no wildcard-domain cookie or CORS relaxation. Child sessions carry their
host and parent-session hash in `site_sessions`, checked on every authenticated
request. Logging out revokes the parent and all children. Private-data, profile
mutation, password authentication, WebAuthn and administrator APIs are blocked
on satellite domains. Only calendar APIs, minimal site identity and public profile
reads are exposed there. A signed child token is not accepted at another host.
The silent guest check does not create a CSRF cookie and race account bootstrap.

`deploy-sites-upgrade.sh` snapshots account and calendar code/config/database,
installs the shared assets and host router, initializes applications as the
service user, tests Nginx and reloads without changing listeners or service units.
It adds only the two campus virtual hosts, using the existing wildcard certificate.
SSO proxy locations suppress error logs containing one-use query credentials.
Rollback restores code/config, not live user data; added tables remain compatible
with the previous session-table layout. Source account files are outside Git;
calendar changes belong in the separate `BETA-SDC/calendar` repository.
