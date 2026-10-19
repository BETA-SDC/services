# Account service architecture

## Scope

This service remains a Flask modular monolith. The architecture now covers the
whole account service: registration/login, GitHub binding, groups and roles,
profiles, preferences, cross-site sessions, 2FA, passkeys, private information,
attendance and mail delivery. This changes organization, not public URLs, schema
version, encryption formats, consent versions or permission rules. It does not
migrate production data or change
listeners, Nginx, firewall rules or background services.

`server/webapp.py` and `registration.create_app` remain the entry points.
`registration/checkin.py` and `registration/checkin_access.py` are compatibility
facades for the factory, tests and maintenance jobs, not implementation modules.
The same compatibility rule applies to `permissions.py`, `private_data.py`,
`passkeys.py`, `sites.py` and `server/mail_worker.py`.

## Account backend

`registration/features.py` is the ordered HTTP feature registry.
The factory owns configuration, dependency setup, security middleware and
registration. It does not own feature implementation. Registration, password
login, email verification, profile updates and email 2FA retain their existing
small controllers; they are not split into artificial per-function files.

| Package | Responsibility |
| --- | --- |
| `registration/access_control/` | Schema, roles/policy, GitHub organization/team snapshots, administration and batched directory reads |
| `registration/privacy/` | Schema, AES-GCM encryption, self identity/session checks, single-use consent and HTTP endpoints |
| `registration/site_accounts/` | Host/redirect policy, profile storage, preferences, one-use SSO, public profiles and shared shell |
| `registration/webauthn/` | RP/origin/user-handle protocol, authenticated enrollment/step-up, discoverable login |
| `registration/storage/` | Transactional initialization, revocable parent/child sessions and atomic authentication attempt limits |
| `mailer/` | Sender configuration, message templates, verified-TLS SMTP transport, queue-specific delivery and batch orchestration |

`RegistrationStore` remains the persistence API. Its two internal operation
mixins use the same `connection()` owner; they do not override one another or
open additional databases. Schema initialization remains one atomic, idempotent
transaction at version 11. There is no new migration framework or data rewrite.
Keep encryption keys, session signatures, SSO parent binding, CSRF checks,
single-use grants and rate limits intact when adding features.

The administrator directory loads ownership and memberships in two bounded
queries per page instead of two queries per returned user (up to 100 previously).
The rest of the directory queries are unchanged. Authorization is still checked
on each request and again inside write transactions; no cross-request identity
or role cache is introduced.

## Account frontend

`main.js` now loads `account/bootstrap.js` as an ES module. The account page
does not depend on three scripts sharing undeclared global bindings.

| Module under `campus-register/account/` | Responsibility |
| --- | --- |
| `context.js` | Explicit in-memory state, DOM references, labels and private-template mounting |
| `client.js`, `ui.js` | Account CSRF rotation/recovery and small rendering/error helpers |
| `views.js`, `auth-events.js`, `signup.js` | Login/registration/legacy verification views, authentication handlers and code cooldown |
| `github.js` | Linked-account presentation and authorization |
| `privacy.js`, `privacy-events.js` | Consent, reveal/edit/delete, passkey management and sensitive-data cleanup |
| `webauthn.js`, `factor.js` | Credential serialization and email/passkey step-up |
| `settings.js`, `settings-events.js` | Panel navigation and settings handlers |
| `profile.js`, `preferences.js` | Public profile/student ID and account-wide color mode |
| `bootstrap.js` | Handler installation before initial session loading |

`shared/http.js` is used by account, administration and attendance. It keeps
same-origin cookies, no-store requests, a 15-second timeout, JSON validation,
header-only credentials and structured errors consistent. Account initialization
reuses its fetched session response when rendering, avoiding another identical
request. Only a `csrf_failed` error resets the account CSRF state: an ordinary
permission denial must not disable the whole login form.

Account assets use an explicit `/account/<name>.js` allowlist; shared assets use
`/shared/<name>.js`. Add entries in `registration/assets.py` when adding modules.
The existing shared site shell and calendar application retain their current
entry points; calendar-specific rendering and its external repository are not
rewritten here.

## Mail worker

The standalone `server/mail_worker.py` still loads systemd credentials and owns
the process lock. Mail templates and SMTP transport are reusable, but queue
handlers retain their individual live-recipient, session, grant, expiry,
deduplication and retry checks. The web process still never sends SMTP directly.

## Attendance backend

All attendance implementation is in `server/registration/checkins/`.

| Module | Responsibility |
| --- | --- |
| `domain.py` | Field classification, consent version, time zone, status and CSV-safe values |
| `validation.py` | Event, introduction and submitted-answer validation |
| `schema.py` | Idempotent creation of existing attendance and approval tables |
| `calendar.py` | Read-only calendar snapshot integration and date filtering |
| `permissions.py` | Member, administrator, creator, verified-account and 2FA checks |
| `repository.py` | Existing connection access, event/options queries, receipts and approval lists |
| `presentation.py` | Explicit public/managed responses and introduction visibility |
| `notifications.py` | Shared encrypted system-mail queue integration |
| `grants.py` | PAT issuance, account/event scope, expiry, revocation and authorization |
| `http.py` | Blueprints, host guards, JSON errors, payload and pagination parsing |
| `pages.py` | HTML, approved module assets, session and introduction endpoints |
| `events.py` | Event creation, event management, calendar choices and QR output |
| `attendance.py` | Consent, deduplication, submission and single-use prefill grants |
| `roster.py` | Server-side field redaction and protected CSV output |
| `deletion.py` | Deletion applications, administrator review and atomic deletion |
| `approvals.py` | Access applications, administrator review and PAT management |

Dependencies flow from HTTP controllers into shared policy, presentation and
storage helpers. Shared helpers must not import controllers or compatibility
facades. The package initializer assembles controllers once; individual Flask
applications can register the same blueprints safely.

Transaction ownership remains in controllers. Helpers use the caller's SQLite
connection, especially when constructing a newly-created event response.
`managed_event` reuses event options rather than reading them twice.
This removes nested connections but does not claim to eliminate all per-event
queries or establish a measured throughput improvement.
Pure GET handlers do not reserve SQLite's writer lock with `BEGIN IMMEDIATE`.
Writes retain their existing explicit transactions. Static attendance modules
still pass host/network checks but do not open a database to purge expired grants.

## Attendance frontend

`campus-register/checkin.js` is an ES-module entry point. No bundler or additional
runtime dependency is required.

| Module under `campus-register/checkin/` | Responsibility |
| --- | --- |
| `bootstrap.js` | Page selection, initialization and visibility lifecycle |
| `context.js` | Page routes, labels and explicit shared in-memory state |
| `ui.js` | CSRF-aware API client, notices, buttons, tables and formatting |
| `events.js` | Event list, creation, calendar selection and QR presentation |
| `attendance.js` | Answer fields, consent, prefill and receipt |
| `records.js` | Basic roster, protected roster, pagination, access requests and export |
| `deletion.js` | Deletion request and review controls |
| `approvals.js` | PAT application review, resend and revocation controls |
| `factor.js` | Shared email/passkey step-up dialog |
| `pages.js` | Public introduction and creator/administrator editor |
| `sensitive.js` | Clearing PAT, fetched profile prefills and rendered sensitive records |

The server exposes only explicitly allowed files at
`/checkin-assets/<name>.js`, with the same host restriction, CSP and no-store
policy as existing attendance routes. Unknown names and path traversal are
rejected. Imports form an acyclic dependency graph. Do not add shared state to
`window`, put PATs in URLs or persistent browser storage, or weaken server-side
authorization based on frontend state.

## Privacy invariants

- Basic roster responses allow only name, email and student ID.
- Phone, document and custom fields require an account/event-bound PAT and fresh 2FA.
- Every CSV export requires a valid PAT, administrator privileges and export scope.
- PATs are issued through encrypted system mail; stored grants contain digests.
- Previously-recorded consent is never upgraded by rewriting history.
- Attendance is not deleted by age. Only transient prefill grants expire.

## Checks

From the project root, using a Python environment with the existing requirements:

```sh
python -B -m unittest discover -s server -t .
node --test 'campus-register/tests/*.test.mjs'
python -B server/release_manifest.py
sh -n server/deploy-service-upgrade.sh
sh -n server/deploy-checkin-governance.sh
```

`test_service_architecture.py` additionally guards whole-service composition,
compatibility imports, schema repeatability, account/shared asset routing,
acyclic feature/frontend dependencies, bounded directory queries and release
coverage. `test_checkin_architecture.py` guards attendance boundaries, public imports,
multi-app registration, frontend asset routing, release completeness,
pagination and connection reuse. Existing end-to-end API tests cover permissions,
PAT issuance/revocation, consent, prefill and deduplication.

Use synthetic accounts and a disposable database for browser regression.
Never create synthetic attendance records in production.

## Release

`server/service-release.json` is the complete account release manifest.
`checkin-release.json` is a generated compatibility copy, not a partial release.
Regenerate both with `python -B server/release_manifest.py --write`; the command
without `--write` checks for drift. Do not put credentials, databases, encryption
keys, dependency caches or local macOS background-item definitions in a release.
For attendance frontend modules, also update `checkins/pages.py`.

Use `server/deploy-service-upgrade.sh`. Its historical implementation entry
`deploy-checkin-governance.sh` now consumes the full manifest and runs the entire
server test suite. It validates staged files before service changes, creates
nested module directories, backs up installed files and the database, runs
server-side preflight tests, and retains its existing code-rollback behavior.
Do not run historical upgrade scripts as the current release procedure.

The source is tracked in a local Git repository on `main`; no remote is configured.
Credentials, runtime data and dependency caches are excluded by `.gitignore`.
This refactor does not establish database migrations.
The surrounding legacy macOS installers, shared calendar Python runtime and
calendar-specific implementation are intentionally unchanged. Future work
should address reproducible dependency ownership, versioned migrations,
restore drills and measured load testing separately.
