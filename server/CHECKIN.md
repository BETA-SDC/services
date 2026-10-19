# Event Check-In

Entry: `https://account.betasdc.duckdns.org/checkin`. The shared navigation
links to this page; attendee QR codes use `/checkin/join/<random-token>`.
No new DNS records, listeners, firewall rules or shared cookies are needed.
Management uses `/checkin` for tables and `/checkin/requests` for the admin-only
deletion queue, plus `/checkin/access-requests` for private-record approval,
with a shared desktop sidebar/mobile top navigation.

## Emailed PAT Approval

- Signed-in verified creators, current BETA-SDC members and admins may request
  record access, stating a purpose. Pending requests are unique per user/table.
  Admins must complete 2FA to approve/reject, select an exact expiry
  (1 minute to 7 days, default UI 1 hour), revoke, or resend.
- A 256-bit `beta_checkin_...` PAT is generated only on approval, bound to
  the applicant's verified email/account and one table. Every sensitive read
  requires that PAT in `X-Checkin-PAT`, the applicant's active login,
  current eligibility, a still-authorized approving admin, and fresh 2FA.
  This includes administrators: there is no role-based PAT bypass.
- Record access is at `/checkin/records/<event-id>`, separate from management
  metadata. PATs are held only in browser memory, never URLs or persistent
  browser storage; leaving/hiding the page or reaching expiry clears the
  input, credential and displayed records. Already downloaded data cannot
  be remotely erased. Export additionally requires admin status and an
  explicit export flag on the approved PAT; normal applicants are read-only.
- Only PAT digests are kept in grants. The plaintext PAT appears only in
  the encrypted transactional mail payload, then in the recipient's email.
  Admin/API responses and audit entries never disclose it. Resending rotates
  the digest without extending expiry; old PATs stop working immediately.
- Request acknowledgements, admin alerts, approval/rejection, PAT delivery,
  revocation, expiry and deletion/setting notices use `BETA SDC Service`
  through the existing SMTP worker. Mail never contains attendee answers.
  SMTP acceptance is not proof of inbox delivery. Failures retry with backoff;
  the admin queue shows pending/accepted/failed delivery and supports resend.
  The encrypted outbox validates recipient email, admin alerts' current role,
  grant validity and token digest before sending. Expiry notices are scheduled
  in that same queue; no additional timer or in-app message center is created.
- Basic reads return only name/email/student ID and timestamp to eligible
  signed-in creators/members/admins, respecting introduction visibility.
  Phone, documents and custom answers are removed server-side, including
  their labels. Sensitive reads and every CSV require PAT plus 2FA.
- New consent v5 discloses direct basic-roster viewing. Ordinary basic
  readers see only v5; PAT holders see v4/v5. Older versions remain admin-only;
  admins still require PAT/2FA for sensitive reads and all exports.
  Approval, recipient, scope, expiration and revocation history are retained.

## Introduction Pages

- Expanded tables link to `/checkin/events/<creator-registration-id>/<title-slug>--<event-id>`.
  Names are URL-encoded, same-name tables remain distinct, and addresses are stable.
- Creation defaults to a public introduction; the creator or an administrator
  can edit the introduction (up to 2,000 characters) and choose public,
  current BETA-SDC members, or creator/admin-only access. Existing tables get
  an empty public introduction via an additive, idempotent migration.
- `/api/checkin/pages/<creator-registration-id>/<slug>` returns only title,
  introduction, creator's real name and live aggregate count to ordinary viewers.
  It never returns participant answers, fields, calendar metadata, QR/join
  secrets or a participant list. Hidden pages return 404 for unauthorized users.
- `/api/checkin/events/<id>/visibility` accepts a CSRF-protected PUT containing
  `description` and `visibility`. Creator identity and admin/membership validity
  are checked on the server for every access; changing introduction visibility
  never grants public access to attendee records or changes QR participation.
- New tables preselect name, email and student ID. Saved-information/login
  actions are highlighted, but single-use consent and private-data 2FA remain.

## Ownership And Data

- Members with live BETA-SDC GitHub membership may create events, view
  their own metadata/QR, and request deletion of their own events.
  They may edit their own introduction/visibility and read eligible basic
  rosters, but cannot read protected fields without PAT/2FA, export, pause, rotate, modify
  the sign-in form or delete it.
  Local admins or unexpired linked GitHub owner grants may create and
  manage all events, including events created by other users.
- `POST /api/checkin/events/<id>/deletion-request` takes a reason.
  Pending requests are unique per event. Admins review the queue with
  `GET /api/checkin/deletion-requests` and approve/reject through
  `POST /api/checkin/deletion-requests/<id>/review`. Review and direct
  deletion require current second-factor proof. Approval atomically
  deletes the event and its attendee data; rejection preserves it.
  Requests, reasons and reviewer/timestamps remain as administrative
  history; replayed decisions return 409.
- Selected fields are all required. At least one of email, student ID,
  mainland phone or identity document is required for duplicate checks.
  Documents use existing type/country/number validation; these are format
  checks, not official identity verification. Reading document records
  requires step-up verification, including organizer list views.
- Up to eight custom, required text entries (60-character label,
  500-character answer) may be added. They are event-scoped, encrypted
  with the answers, never populated from unrelated profile data, and
  never used as unique identity keys.
- The creator may select an event from the local authoritative calendar
  snapshot by date (default: today in Asia/Shanghai). The selector API
  returns only events overlapping that day, including overnight events;
  all-day and timed end dates are exclusive. Changing dates clears the
  selected association and stale responses cannot replace newer results.
  The form copies title, location and eligible times; changing
  these does not change the calendar. Association UIDs are validated and
  stored in an additive options table and never exposed to attendees.
  The selector is member-only; a missing snapshot does not block manual
  creation. Calendar state defaults to `CALENDAR_STATE`, or
  `/var/lib/betasdc-calendar`; tests use `CHECKIN_CALENDAR_STATE`.
- Attendee submission requires the current event policy and explicit
  consent. Only the server assigns timestamps. Guest identity and physical
  presence are not verified; this is not an anti-impersonation system.
- SQLite `BEGIN IMMEDIATE`, an account unique index and event-scoped
  keyed identifier digests prevent concurrent duplicate records. A signed
  browser receipt makes guest retries idempotent; an authenticated user
  can retrieve only their own timestamp after a retry.
- Answers and join-token secrets use the existing private-data AES-GCM
  key, with event/entry-separated authenticated contexts. The key is never
  replaced silently when check-in or private-profile ciphertext exists.
- Prefill is a separate, 60-second single-use grant bound to the current
  user, session and event. Private profile fields require the existing
  email/passkey step-up proof for phone/documents, not student IDs.
  Student IDs remain encrypted and self-only in ordinary profile settings.
  Prefill never submits the form.
- `/api/profile/student-id` reads/updates only the authenticated user's
  student ID without step-up. New registrations require a student ID, saved
  atomically with the account. Updates cannot clear it. Existing accounts
  without one can still log in and add it. Updates require CSRF, validate syntax,
  revoke outstanding profile/prefill grants and never expose phone/documents.
  Existing encrypted student IDs are reused without a storage migration.
- Export requires an explicitly export-enabled PAT, admin status and a fresh
  step-up proof, and neutralizes CSV formula
  prefixes. Downloaded CSVs are outside the server's encryption/retention
  controls. Views and exports record field names, never answer values,
  in the existing audit table.
- Events, entries, duplicate digests and options never expire automatically.
  The legacy `retain_until` column remains for database compatibility
  but is no longer used for deletion (new and upgraded rows store a
  year-9999 compatibility sentinel, preventing legacy rollback cleanup).
  `purge_expired` removes transient authorizations only; it never removes
  attendance. The legacy cleanup timer and service stay stopped/disabled.
  Existing database-backup rotation is unchanged.
- New submissions require policy v5, explicitly disclosing long-term
  storage, direct eligible basic readers and approved limited-time private readers. Historical entry policy values
  are not rewritten or treated as new consent. Operators must separately
  notify/confirm any extension of earlier retention promises.

## Runtime

Install `server/requirements-checkin.txt` into `server/_vendor` with the
server's Python, keeping the upstream wheel metadata/license. SVG generation
uses python-qrcode; it does not require Pillow or an external QR service.

`server/deploy-checkin-upgrade.sh STAGED_SOURCE PINNED_WHEEL` upgrades the
existing Linux installation as root, backs up code and the SQLite database,
tests against synthetic temporary databases, preflights the shared app,
reloads Gunicorn and disables the legacy cleanup timer. It does not alter
Nginx or listeners. Failed deployments restore code, not the live database,
to avoid deleting concurrent registrations. Schema additions are backward
compatible.

## Checks

```sh
python -m unittest server.test_checkin server.test_sites
python -m unittest server.test_checkin_access
python -m unittest discover -s server -t .
node --check campus-register/checkin.js
node --check campus-register/beta-shell.js
sh -n server/deploy-checkin-upgrade.sh
sh -n server/deploy-checkin-governance.sh
```

For UI tests use a separate loopback fixture/database. Do not create accounts,
2FA proofs or sample attendees in production for testing. Verify desktop and
mobile forms in both color modes, real QR decoding, manual guest submission,
registered-user step-up/prefill, server-timestamp receipts and organizer
records. Production verification should only read assets, anonymous session
capabilities and service/timer status.

Local verification on 2026-10-17: all 177 tests passed. A separate loopback
browser fixture exercised guest submission, member requests, administrator
email step-up and approval, encrypted outbox delivery through a synthetic
SMTP server, recipient step-up and PAT reads, revocation and denied reuse.
Desktop/mobile light and dark layouts, public introduction isolation and
clearing records/PAT on page hiding were checked. No real SMTP delivery or
production deployment was performed; the user requested local testing only.

Production deployment on 2026-10-18: 98 server preflight tests passed; all
22 installed file hashes matched the tested source. Anonymous read/access
endpoints reject unauthenticated requests, and assets and the v4 session
endpoint respond successfully. Application services and the mail timer are
active; the mail worker completed successfully and attendance cleanup stays
disabled. No production sample rows or real PAT emails were generated for
verification.

2026-10-19 basic-roster split: 180 local tests and 107 production preflight
tests passed. Synthetic browser checks verified direct basic display without
PAT, server-side redaction of phone/custom fields, no export without PAT,
and desktop/mobile light/dark layouts. No production sample entries created.

## Code Organization

The attendance backend now lives in `registration/checkins/`; the frontend lives
in `campus-register/checkin/` and loads through the existing `/checkin.js` entry.
See [ARCHITECTURE.md](ARCHITECTURE.md) for module ownership, security invariants,
test commands and release-manifest requirements. Existing URLs and consent
versions are unchanged.

2026-10-19 modularization verification: 191 backend tests and 5 frontend tests
passed. Synthetic browser checks exercised event creation, calendar selection,
QR presentation, introduction editing, email step-up, profile prefill,
attendance submission, basic roster redaction, administrator PAT approval,
encrypted mail delivery, protected roster reads and return to basic view.
Desktop light and mobile dark screenshots were reviewed; the mobile document
did not overflow horizontally. The expected 401 step-up challenge was handled.
No production deployment or real SMTP delivery was performed during those tests.
