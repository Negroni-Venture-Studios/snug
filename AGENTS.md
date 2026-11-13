# AGENTS.md: working on Snug

Instructions for AI coding agents working in this repository. People will find them useful too; [CONTRIBUTING.md](CONTRIBUTING.md) is the human-oriented version.

## What Snug is

A self-hosted data room for early-stage founders, published as MIT-licensed open source by Negroni Venture Studios. One instance serves one company, on the founder's own server.

- **[docs/design.md](docs/design.md) is the source of truth.**
- Planned work is in GitHub issues labelled `enhancement`. New work that is deferred becomes an issue, not a note in docs/design.md.
- If something you need is missing from docs/design.md, or contradicts it, **ask rather than deciding silently.**

## Commands

```bash
uv sync                            # install everything, from uv.lock
uv run pytest                      # tests
uv run pytest --cov                # tests with the coverage floor
uv run playwright install chromium  # once, for the end-to-end tests
SNUG_E2E=1 uv run pytest tests/e2e --ds=tests.e2e_settings  # a real browser, as CI runs it
uv run ruff check . && uv run ruff format --check .
uv run mypy                        # strict
uv run python scripts/check_licences.py
uv run pre-commit run --all-files  # lint, types, tests, and licences; CI also runs coverage, the browser tests, and the security scans
python3 docs/diagrams/build.py     # after changing a diagram in docs/diagrams/
SNUG_E2E=1 SNUG_SCREENSHOTS=1 uv run pytest tests/e2e/test_screenshots.py --ds=tests.e2e_settings  # after changing a page
```

Always use `uv run`. Never install into the system Python.

## Layout

| Path | What |
|---|---|
| `snug/` | The Django project: settings and configuration, and the apps `accounts`, `analytics`, `audit`, `core`, `documents`, `links`, `nda`, `rendering` (the confined worker that reads PDFs, Office files, and MP4 videos), `rooms`, and `viewer` |
| `tests/` | pytest suite; `tests/e2e/` drives a real browser with Playwright |
| `scripts/` | CI and maintenance scripts (`check_licences.py`) |
| `docs/` | Operator documentation, `design.md` on how Snug is designed, and `architecture.md` on how the code works; `docs/design/` holds the mockups, stylesheet seed, and logo; `docs/diagrams/` the diagrams' sources and `build.py` |
| `Dockerfile` | The container image, published by the `Image` workflow |
| `deploy/` | `compose.yaml` (Snug, Caddy, Litestream, and the rclone backup sidecar), `Caddyfile`, `litestream.yml`, `backup.sh`, `restore.sh`, `setup.sh`, `update.sh`, `snug` (the server's command), `cloud-init.yaml`, `rclone.conf.example`, and `snug.env.example` |
| `.github/` | CI workflows, CodeQL, Dependabot, templates |

## Rules

### Git and pull requests

- `main` is protected. **Never commit to `main` or push to it.** Create a branch, push it, open a pull request, and let CI pass.
- Keep pull requests small and focused. A larger change is several of them, each leaving the suite green.
- Commit messages are in the imperative mood, with a first line under 72 characters.
- When you author or co-author a commit, end the message with this trailer:

      Co-Authored-By: Claude <noreply@anthropic.invalid>

  Do **not** use `noreply@anthropic.com`. That address resolves to an unrelated GitHub account. The `.invalid` top-level domain is reserved and never resolves.
- Add a line under `[Unreleased]` in CHANGELOG.md for anything a user or operator would notice.

### Test first

Write the test that proves the change, and confirm it fails for the reason you expect, before you change the code. Choose inputs that cannot coincide with a default or fallback value, or a wrong answer can pass.

### Security requirements (non-negotiable)

1. **One authorisation path.** Every request for viewer content goes through a single function, which checks in order:
   - the link exists, is not revoked, and has not expired;
   - the session is valid and bound to this link and this email address;
   - the NDA has been accepted, if one is required;
   - the geographic restriction is met, if one is set;
   - the requested document belongs to the link's document or room.

   No viewer endpoint may skip it. A test walks the URL configuration to enforce this.
2. **No guessable identifiers** in viewer URLs. Use random tokens, never database keys.
3. **Tests for every authorisation rule**, including revoked links with open sessions, expired links, link A's session used against link B, a document outside the room, and regressions for path traversal, unauthenticated upload, permissive CORS, and cross-resource admin actions.
4. **No CORS headers.**
5. **A strict content security policy.** No inline scripts, no inline styles, and no `on*=` handlers, ever.
6. **CSRF protection** on every state-changing admin request.
7. **Rate limits** on login, code requests, and code verification.
8. **File handling.** Store uploads under generated names. Resolve paths from database records, never from request parameters. Read and render PDFs, check and stamp Office files, and check videos, only in the worker process: a timeout and a memory limit, confined by Landlock and a seccomp filter that refuses sockets and any reach into another process, and refusing to read anything if it cannot confine itself or set its memory limit.
   The optional LibreOffice converter (`snug/office/`, `converter/`) keeps these rules too, and the worker's stay as they are: it runs in a container of its own, under gVisor, with no network, a read-only root, no capabilities, and its own user, converting one file per container; LibreOffice gets the import filter for the kind Snug's checker found, a fresh profile with macros and link updates off, and the seccomp filter with Unix sockets only, and converts nothing if the filter cannot be applied; and Snug reads its answers without following links, then has the confined worker read every PDF it makes.
9. **Never log** tokens, one-time codes, session identifiers, or passwords.
10. **Dependencies.** Ask before adding one, and check that its licence is MIT-compatible (`scripts/check_licences.py`).
11. **Calls out of Snug** over HTTP go only to Google (the Gmail API, for email) and DB-IP (the country data), through `snug.core.outbound.urlopen`, which takes proxy settings from the environment only. The default urllib opener asks macOS for them, which kills a forked gunicorn worker. Otherwise Snug connects only to the SMTP server set up for email, and `snug doctor`, and housekeeping every hour, to Snug's own domain. No test may reach the internet; a fixture in `tests/conftest.py` blocks both.

### Front end

- Follow docs/design.md section 3:
  - Geist and Geist Mono;
  - the Negroni-derived tokens in `docs/design/snug.css`;
  - dark by default;
  - Phosphor icons, vendored from v2.0.8 into `snug/static/snug/icons/`;
  - server-rendered SVG charts.
- Every text colour pairing must pass WCAG AA (4.5:1) in both themes.
- Interface copy is British English, in sentence case, with no em or en dashes and no middle dots used as separators.
- Every admin save returns to its own section with a green notice of what changed (`snug.core.notices`, or the `saved` query in Settings); errors show in red beside the field. Where a save leads to a next step, as in the Email steps, it lands on that step, with the notice there.
- Verify a UI change with a real click in a browser, not only a screenshot. The CSP silently kills inline handlers, and a screenshot cannot show a dead click.

### Versions

Pin new dependencies, tools, and GitHub Actions to releases at least two weeks old. Pin Actions by commit SHA, with the version in a comment.

The exception is a **security release from an upstream we already depend on**, such as a Django patch release that fixes a CVE. Take it at once, and say so in the pull request.
