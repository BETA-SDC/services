# Account service architecture

## Scope

This service remains a Flask modular monolith. This refactor changes attendance
code organization, not public URLs, database schemas, encryption formats, consent
versions or permission rules. It does not migrate production data or change
listeners, Nginx, firewall rules or background services.

`server/webapp.py` and `registration.create_app` remain the entry points.
`registration/checkin.py` and `registration/checkin_access.py` are compatibility
facades for the factory, tests and maintenance jobs, not implementation modules.

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
node --test campus-register/tests/checkin-ui.test.mjs
sh -n server/deploy-checkin-governance.sh
```

`test_checkin_architecture.py` guards dependency boundaries, public imports,
multi-app registration, frontend asset routing, release completeness,
pagination and connection reuse. Existing end-to-end API tests cover permissions,
PAT issuance/revocation, consent, prefill and deduplication.

Use synthetic accounts and a disposable database for browser regression.
Never create synthetic attendance records in production.

## Release

`server/checkin-release.json` is the release file manifest consumed by
`deploy-checkin-governance.sh`. Add every new backend module and frontend module
to it. For frontend modules, also update the allowlist in `checkins/pages.py`.
The architecture tests verify these stay aligned.

The deployment script validates staged files before service changes, creates
nested module directories, backs up installed files and the database, runs
server-side preflight tests, and retains its existing code-rollback behavior.
Do not run historical upgrade scripts as the current release procedure.

The source is tracked in a local Git repository on `main`; no remote is configured.
Credentials, runtime data and dependency caches are excluded by `.gitignore`.
This refactor does not establish database migrations.
The surrounding legacy macOS installers, shared calendar Python runtime and
non-attendance modules are intentionally unchanged. Future architecture work
should address version control, reproducible dependency ownership, migrations,
restore drills and measured load testing separately.
