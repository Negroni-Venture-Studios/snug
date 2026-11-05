# Changelog

All notable changes to Snug are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and Snug uses [semantic versioning](https://semver.org/).

## [Unreleased]

### Added

- Signing in to the admin interface:
  - email and password, then a code from the authenticator app or a recovery code;
  - rate limits per address and per account;
  - idle and absolute session limits;
  - every sign-in, failure and sign-out recorded in the audit log.
- The front end from DESIGN.md revision 2, in production: the stylesheet, Geist fonts, the theme toggle (dark by default), the logo and the sign-in pages.
- `SNUG_TRUSTED_PROXIES`, so the client address is taken from `X-Forwarded-For` only when the request comes from Caddy.
- Admin accounts:
  - `snug createadmin`, with argon2id passwords, authenticator enrolment by QR code, and ten single-use recovery codes;
  - `snug reset-2fa` for an admin who has lost their phone (`docs/admin-accounts.md`).
- An append-only audit log, enforced by the database as well as the code. It records admin creation and second-factor resets.
- The Django project skeleton:
  - configuration from environment variables, checked at start-up with every problem listed at once (`docs/configuration.md`);
  - SQLite in WAL mode;
  - strict security headers on every response, and no CORS;
  - a `/healthz` check;
  - `snug serve`, which applies migrations, then starts gunicorn.

- Repository guard rails: CI for lint, type checking, tests on Python 3.12 and 3.14, secret scanning, dependency audit, licence checks and a software bill of materials; CodeQL; Dependabot; pre-commit hooks.
- Project documents: README, contributing guide, security policy, code of conduct, trademarks note, and agent instructions.
- DESIGN.md, recording the architecture, the agreed decisions and the front-end design, with clickable mockups and the Snug logo in `docs/design/`.
- The original project brief, kept for the record in `docs/BRIEF.md`.

[Unreleased]: https://github.com/Negroni-Venture-Studios/snug/commits/main
