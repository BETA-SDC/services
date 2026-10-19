# Open-Source References

## BETA Brand

The logo supplied by the user is retained unchanged in
`campus-register/brand-logo.png`:
https://github.com/BETA-SDC/community/blob/main/logos/beta_college_vi_logo_lockup_full_color.png

`brand-logo-display.png` trims only transparent outer margins for page
layout; all visible artwork and original colors are preserved.
`beta-favicon-v2.ico` includes the complete mark, centered with transparent
padding, at six icon sizes. No ownership or license of the BETA
brand is claimed by this project; follow the upstream brand usage rules.

`campus-register/default-avatar.png` is the user-supplied BETA mascot,
downloaded unchanged and served locally for accounts without a GitHub avatar:
https://github.com/BETA-SDC/community/blob/main/logos/beta_mascot_AI_clarified.png
The same brand ownership and usage restrictions apply.

The original Betasdc authentication UI was informed by these primary sources:

- Cal.com login view: semantic form fields, password visibility, submission
  state, and generic authentication errors.
  https://github.com/calcom/cal.com/blob/main/apps/web/modules/auth/login-view.tsx
- Outline login scene: restrained centered layout, clear authentication
  choices, and loading/error states.
  https://github.com/outline/outline/blob/main/app/scenes/Login/Login.tsx
- Flask tutorial authentication blueprint: Werkzeug password hashes and
  clearing the prior session when signing in.
  https://github.com/pallets/flask/blob/main/examples/tutorial/flaskr/auth.py

These projects were studied as references, not copied or forked. Betasdc uses
its existing Flask/SQLite architecture; it does not claim their OAuth,
email-delivery, or account-recovery features.

## Bundled Icons

`campus-register/icons.js` is the unmodified Lucide 0.468.0 UMD distribution,
self-hosted rather than loaded from a third-party CDN at runtime.
Upstream: https://github.com/lucide-icons/lucide/tree/0.468.0
License: ISC, including Feather-origin portions described in the upstream
notice. The complete upstream notice is retained in
`campus-register/LICENSE.lucide.txt`.

## Event Check-In

The check-in workflow is informed by pretix's event-scoped check-in lists,
required answers, duplicate checks and idempotent retries. No pretix code is
copied; this implementation uses the existing Flask/SQLite account service.
Primary reference:
https://github.com/pretix/pretix/blob/master/doc/api/resources/checkinlists.rst

QR codes are generated locally by python-qrcode 8.2 (BSD-3-Clause), with a
standard four-module quiet zone and medium error correction. No attendee data
is sent to an external QR service. The upstream license is included in the
installed `qrcode-8.2.dist-info/LICENSE` metadata.
https://github.com/lincolnloop/python-qrcode/tree/v8.2
