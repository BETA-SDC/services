# Betasdc

Stable project path: `/Users/jietexue/_projects/betasdc`.
Current project overview: [OVERVIEW.md](OVERVIEW.md), updated 2026-10-16.
Source files were moved here on 2026-10-14. The previous chat output directory
is no longer the source location.

## Layout

- `server/registration/`: Linux application factory, configuration, routes,
  validation, database repository, and shared access checks.
- `server/`: stable Gunicorn entry point, application tests, Linux deployment
  scripts, and systemd/Nginx configuration.
- `campus-register/`: shared frontend assets and certificate-renewal wrapper;
  other scripts, tests, and service templates are from the former Mac deployment.
- `duckdns/`: campus-IP updater source and deployment documentation.

## Deployed State

The active deployment moved to Ubuntu server `550c.duckdns.org` on 2026-10-14.
Open `https://account.betasdc.duckdns.org` from a network with a route to the campus server.
No fixed client-IP subnet whitelist is used on any BETA site or SSO. See
`server/README.md` for the deployment and SSH configuration.

Registration and login are available at `/register` and `/login`. The
authentication upgrade preserves existing password-less records without
allowing public takeover. It provides hashed passwords and revocable
sessions, registration-time email codes via a separate 163 SMTP worker,
and optional GitHub account binding via an isolated OAuth broker.
Password recovery is not implemented.
See `THIRD-PARTY.md` for the open-source reference projects and icon license.

The Mac deployment described below is retained as a stopped backup. Its
registration, DuckDNS, and certificate-renewal LaunchAgents are disabled.

Migration did not move credentials, database, certificates, or running services.
The services already use stable deployed copies outside the chat directory:

- App, database, and deployed certificates:
  `~/Library/Application Support/BetasdcDemo/`.
- DuckDNS updater and credential:
  `~/Library/Application Support/DuckDNS/`.
- ACME client, certificate profiles, and renewal credentials:
  `~/Library/BetasdcACME/`.
- User service configurations: `~/Library/LaunchAgents/`.

Changes to source files here are not automatically deployed to the running app.
Never copy credentials or registration data into the source tree.

The former Mac URL was `https://register.betasdc.duckdns.org:8443`.
Do not enable the following Mac installation while the server is active:

```sh
sudo /bin/sh /Users/jietexue/_projects/betasdc/campus-register/enable-nginx-443.sh
```

See `campus-register/README.md` for privilege safeguards, stopping services,
certificate renewal, and migration to a server.

## Checks

Current Linux application (requires Flask):

```sh
cd /Users/jietexue/_projects/betasdc
PYTHONDONTWRITEBYTECODE=1 python3 -W error::ResourceWarning \
  -m unittest discover -s server -p 'test_*.py' -v
/bin/sh -n server/install.sh
```

The following checks cover the legacy Mac deployment:

```sh
cd /Users/jietexue/_projects/betasdc/campus-register
/usr/bin/python3 -W error::ResourceWarning -m unittest -v
/bin/sh -n enable-standard-https.sh
/bin/sh -n enable-nginx-443.sh
/bin/sh -n reload-nginx.sh
/bin/sh -n sync-nginx-certificates.sh
/bin/sh -n renew-certificate.sh
/usr/bin/plutil -lint org.betasdc.registration-https.plist
/bin/sh enable-standard-https.sh --check org.betasdc.registration-https.plist
/bin/sh -n ../duckdns/update.sh
```

This project is outside a Git repository. No repository was created during
migration, and no commit or push was performed.
