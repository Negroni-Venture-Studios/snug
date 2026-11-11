# How Snug works

Snug is a Django application that runs in Docker on one server. [DESIGN.md](../DESIGN.md) records why it is built this way, and [AGENTS.md](../AGENTS.md) the rules for changing it.

## On one server

Snug runs as four containers under Docker Compose, from [`deploy/compose.yaml`](../deploy/compose.yaml):

- **Caddy** answers on ports 80 and 443, gets and renews the HTTPS certificate, and passes every request to Snug.
- **Snug** runs the Django application with gunicorn. It is the only container that reads `snug.env`, the settings file with the secret key.
- **Litestream** copies every change to the database into a replica in the same data volume. It has no network.
- **Backup** runs rclone every 5 minutes, encrypting the replica and the uploaded files and copying them to the founder's bucket. It is on its own network, so it cannot reach Snug.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/diagrams/overview-dark.svg">
  <img alt="Founders and investors reach Snug through Caddy. Inside the Snug container, gunicorn's web workers serve requests and start a confined document worker for each job. Litestream and rclone share the data volume and copy it, encrypted, to the founder's bucket. Snug sends email through Google or an SMTP server and fetches country data from DB-IP." src="images/diagrams/overview-light.svg">
</picture>

Every container has a read-only filesystem, drops every Linux capability (Caddy keeps one, to listen on ports 80 and 443) and cannot gain new privileges. The Snug, Litestream and backup containers share one volume, `snug-data`, mounted at `/var/lib/snug`, and all three run as the same unprivileged user (10001).

Inside the Snug container there are three kinds of process:

- **The gunicorn master** starts and watches the web workers. A thread in it runs `snug housekeeping` as a fresh process when Snug starts and every hour after.
- **Two web workers**, each with four threads (gunicorn's `gthread` class), serve the requests.
- **Document workers**: a web worker starts one for each job that reads an uploaded file, such as checking an upload or drawing a page. It confines itself before it parses anything, and exits when the job is done.

## The code

| Path | What it holds |
|---|---|
| `snug/__main__.py` | The `snug` command: loads the settings, creates the data folders, then hands over to Django's commands |
| `snug/config.py` | Reads every `SNUG_*` setting once, into a frozen `Config` |
| `snug/settings.py`, `urls.py`, `wsgi.py`, `gunicorn_conf.py` | Django's settings, the URL map, and gunicorn's hooks |
| `snug/accounts/` | Admins: sign-in, the authenticator, recovery codes, invitations and resets, and the Settings pages |
| `snug/documents/` | Documents and their versions: uploads, the checks on them, and where files are stored |
| `snug/rendering/` | The document worker and the code that starts it |
| `snug/links/` | Links: creating, emailing, revoking and reissuing them |
| `snug/rooms/` | Rooms, their folders and the documents in them |
| `snug/viewer/` | Everything an investor sees, under `/v/`, and the one authorisation path |
| `snug/nda/` | The NDA's versions and each acceptance |
| `snug/analytics/` | Visits and reading time, the People page, the charts, first-open emails, and deleting old reading data |
| `snug/audit/` | The audit log and its CSV export |
| `snug/core/` | Shared parts: the Overview, settings stored in the database, email, rate limits, `snug doctor`, GeoIP, branding and middleware |
| `snug/templates/`, `snug/static/` | The pages, the stylesheet, the fonts, the icons and the scripts |

`snug.rendering` is kept out of `INSTALLED_APPS`. The document worker imports it without Django, and without any of Snug's settings.

## Starting up

`snug` is a console script, `snug.__main__:main`. Before anything else it marks its process as non-dumpable, so no other process running as the same user can read its memory or environment through `/proc`. It then reads the optional `--env-file`, loads the settings and creates the data folders.

`snug.config.load_config` reads the `SNUG_*` environment variables. It checks all of them before it stops, so Snug reports every problem in a broken `snug.env` at once, and the command exits with status 2. The values sit in `settings.CONFIG`, which the rest of the code reads instead of the environment. [docs/configuration.md](configuration.md) lists them.

`snug serve` runs the database migrations, then replaces itself with gunicorn: two workers, four threads each, no access log. The database is SQLite in WAL mode, with a 5-second busy timeout and every transaction started as `IMMEDIATE`, so two threads writing at once wait their turn without deadlocking.

## A request's path

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/diagrams/request-path-dark.svg">
  <img alt="A request passes through Caddy to a gunicorn web worker, then Django's middleware in order, and the URL router sends it to the admin views, the viewer views (every request for content through authorise()) or the public branding files. HTML, page images, video in byte ranges, SVG charts and stamped downloads come back." src="images/diagrams/request-path-light.svg">
</picture>

Caddy passes each request to `snug:8000` and adds the visitor's address in `X-Forwarded-For`. Snug uses that header only on requests from Caddy's fixed address on the Compose network, the `SNUG_TRUSTED_PROXIES` setting, so a visitor cannot forge their address in the audit log (`snug.core.client.client_ip`).

The middleware runs in this order:

1. `forwarded_proto` drops the `X-Forwarded-Proto` header unless the request came from a trusted proxy, as for `X-Forwarded-For`, so a visitor cannot pass a plain HTTP request off as HTTPS.
2. Django's `SecurityMiddleware`, then its content security policy, then `X-Frame-Options`. The policy allows only Snug's own address, so no inline script or style can run.
3. `extra_security_headers`: `Permissions-Policy` and `Cross-Origin-Resource-Policy`.
4. `health_check` answers `/healthz` for Docker's health check, before the host check.
5. WhiteNoise serves `/static/`.
6. Sessions and Django's common middleware.
7. Authentication, then `session_limits`, which signs an admin out after an hour without activity, or 12 hours after signing in.
8. `upload_size_limit` refuses an oversized POST from its declared length, before anything reads the body. Anyone but a signed-in admin is allowed 64 KB, enough for a form's fields. The refusal is a 413, or the sign-in page for an admin whose session has ended. It runs before the CSRF check, since that would read the body to find the token.
9. CSRF.

Each view that needs a rate limit calls `snug.core.ratelimit.allow` with a `Limit` (a scope, a count and a window), or `exceeded` to look without counting. The counts are kept in the database, in fixed windows, keyed by a hash of the scope and the address, account or link.

The URL map sends a request to one of three places:

- **`/admin/`**: the admin interface. Every view needs a signed-in admin; `tests/test_admin_urls.py` checks that each one outside a short public list redirects to the sign-in page.
- **`/v/<token>/`**: everything an investor sees. Every request for a document or room goes through `authorise()`.
- **`/brand/`**: the company's logo, icon and accent colour, which every investor's page loads before anyone has signed in.

## Admins

An admin signs in with a password, then a six-digit code from an authenticator app or one of ten recovery codes. Passwords are hashed with Argon2id. The code check (`snug.accounts.totp`) allows one step either side of now and refuses a code already used. Between the two steps, the session holds only a pending sign-in, valid for 5 minutes and five wrong codes. It records a hash of the password and authenticator secret the first step checked, and the code step compares it with the account as it is now, in the same transaction that spends the code. If either has changed in between, the admin goes back to the password step and the code is not spent.

Admin sessions are Django's own, stored in the database. The hash that ties a session to its admin covers the password and the authenticator secret, so changing either ends that admin's sessions. Only a new password chosen in Settings leaves the browser it was chosen in signed in (`update_session_auth_hash`).

A few changes need a fresh code from the admin's authenticator, even in a session already signed in: inviting, removing or resetting an admin, changing a password, and every change to how email is sent. Replacing your own authenticator takes your password and a code from the old app or a recovery code. `snug.accounts.setup.confirm` checks the code. Every attempt counts towards a rate limit, and each failure is recorded in the audit log.

Invitations, resets and forgotten-password links are `AdminSetup` rows. Each link holds a random token, and the database keeps only its SHA-256 hash.

## Investors and the one authorisation path

An investor reaches Snug through a link, `/v/<token>`. The token is 32 random bytes; the database keeps its SHA-256 hash, so a copy of the database cannot be turned back into working links.

When a link requires a code, Snug emails six digits to the link's address (`snug.viewer.codes`). The code lasts 10 minutes and allows five wrong tries. A correct code starts a viewer session: a random token in the `__Host-snug-viewer` cookie, its hash in a `ViewerSession` row tied to that link and that email address. A link without a code starts an unverified session when the investor presses the *Open* button.

Every view under `/v/` that shows anything goes through one function, `snug.viewer.access.authorise`, which checks five things in order:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/diagrams/authorise-dark.svg">
  <img alt="authorise() checks five things in order: the link is live, the session belongs to this link and email, the NDA is accepted, the visitor is in the UK if required, and the document belongs to the link or its room. Each failure leads to its own page; success returns the link, session and document to the view." src="images/diagrams/authorise-light.svg">
</picture>

Each check raises its own exception, and the decorator `viewer_content` turns each into a response: `Unavailable` into one 404 page, the same for an unknown, expired or revoked link and for a document outside the link, `NotVerified` into the page that asks for a code, `NeedsNda` into a redirect to the NDA, and `OutsideUK` into a 403, recorded in the audit log. On success the view gets an `Access` with the link, the session and the document.

Three views come before a session exists: sending a code, checking it, and opening a link without one. They use `viewer_gate`, which checks only that the link is live and the visitor's country. The NDA page uses `viewer_nda`, which runs `authorise()` without the NDA check it exists to pass.

Each decorator marks its view, and `tests/test_viewer_urls.py` walks Django's whole URL map. It fails if any URL under `/v/` lacks the mark, or takes a database key, or anything but a token, a page number or a width.

A room link's documents are checked on every request against the room's current contents (`RoomDocument`), so removing a document from a room closes it to every link at once.

## The data

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/diagrams/data-model-dark.svg">
  <img alt="Snug's tables in five groups (admins, rooms, documents, sharing and reading) with their many-to-one relations, and the tables that stand alone beneath: the audit log, the site and email settings, the stored checks and the rate limits." src="images/diagrams/data-model-light.svg">
</picture>

- A `Link` points at a document or a room, never both; a check constraint in the database holds it to exactly one.
- A link always shows its document's newest version. `Visit.version_number` records which version was read, as a number, so deleting an old version would not delete the reading data.
- `AuditEvent` rows can be added but never changed or deleted. The model refuses it, and so do SQLite triggers created by the audit app's first migration.
- An `NdaAcceptance` holds the name, email, address, browser and a SHA-256 hash of the exact text accepted. Its foreign keys are protected, so a link someone has accepted the NDA on cannot be deleted.
- `SiteSettings` and `EmailSettings` each hold one row. They keep what admins change in Settings, so a change needs no restart. `EmailSettings` holds the SMTP password, the Google client secret and the refresh token as they are, since Snug needs them to send email.

Files live in the data folder, `SNUG_DATA_DIR`:

```
/var/lib/snug/
├── snug.sqlite3          the database, with its -wal and -shm files
├── originals/            uploads, under random names: <32 hex>.pdf, .xlsx or .mp4
├── renders/              page images, a cache: <version id>/<page>-<width>.webp
├── tmp/                  uploads in progress
├── branding/             the logo and icon
├── geoip/                DB-IP's country data, fetched monthly
├── outbox/               email on a local copy without SMTP
└── backup/               Litestream's replica, and the backup's status files
```

`snug.documents.storage` builds every path from a database record, never from a request, and checks that it stays inside its folder.

## Uploading a document

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/diagrams/upload-dark.svg">
  <img alt="An upload passes a size limit, is received by Django, copied, hashed and given a kind from its first bytes, checked against earlier versions and inspected by the confined worker, then saved as a new version in one transaction and audited. The steps marked with a dot can refuse it with a plain message beside the form." src="images/diagrams/upload-light.svg">
</picture>

Django receives the upload first, in memory if it is under 2.5 MB and in `tmp/` if not. `snug.documents.ingest` then makes it a version, with `check` for the first three steps and `store` for the last:

1. It copies the upload to a temporary file in `tmp/`, computing its SHA-256 as it goes and keeping the first kilobyte. As soon as the first bytes show a video, the video limit applies (`SNUG_MAX_VIDEO_MB`); otherwise the document limit does (`SNUG_MAX_UPLOAD_MB`).
2. The first bytes set the kind: `ftyp` at byte 4 is an MP4 video, a zip is an Excel workbook, and `%PDF-` is a PDF. An old `.xls` file, or a workbook with a password, is refused with its own message. Every version of a document must be the same kind.
3. A document worker checks the file. Snug has written nothing to the database yet, so no other write waits for the check. For a PDF it returns the number of pages and their sizes; for a workbook, its visible sheets, after refusing macros, ActiveX and embedded objects; for a video, its length and picture size, after checking its codecs.
4. Only then, in one transaction, Snug writes a `DocumentVersion` and renames the temporary file into `originals/` under a random name. The name the file was uploaded with is kept for display only.

A refusal at any step shows a plain message beside the form and records `document.upload_refused`. The temporary file is always deleted, and a new document is created only once its file has passed, so a refused upload leaves nothing behind but the audit entry.

## The document worker

Every uploaded file is untrusted. Snug reads them only in a separate process, the document worker (`snug.rendering.worker`), which confines itself before it parses a byte.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/diagrams/worker-dark.svg">
  <img alt="Snug opens the file and starts the worker with a clean environment and the file as a descriptor. The worker limits its resources, loads its libraries, reads the input, confines itself with Landlock and a seccomp filter, refuses to continue unless both are in place, then answers on a pipe that Snug reads up to a size limit and a deadline." src="images/diagrams/worker-light.svg">
</picture>

`snug.rendering.sandbox.run` starts `python -I -m snug.rendering.worker MODE FD`:

- with an environment of `PATH`, `LANG` and `PYTHONDONTWRITEBYTECODE` alone, so no `SNUG_*` setting reaches it;
- in its own process group;
- with the file already open, passed as a file descriptor, never as a path;
- with standard input and errors on `/dev/null`, and its output on a pipe.

The worker then:

1. limits itself to 1 GiB of memory, 60 seconds of CPU, 64 MB written to any file and 32 open files, with no core dumps and no new processes;
2. loads PDFium and Pillow, and runs pypdf and the workbook reader once on tiny files, so nothing has to load later;
3. reads its input, except a video, which it reads in pieces once it is confined;
4. confines itself: no new privileges, then a Landlock ruleset that allows nothing but reading the system's fonts and refuses TCP, then a seccomp filter that refuses to create any socket, or to trace, signal, re-prioritise or change the limits of another process;
5. refuses to go on unless Landlock ABI 4 or later (Linux 6.7), the filter and the memory limit are all in place;
6. does its job and writes the answer to the pipe: a line of JSON, or the image or file it made.

Snug reads the pipe in 64 KiB pieces up to 64 MB (more for a stamped download, which can be as large as the file), against a deadline: 60 seconds to check an upload or stamp a download, 30 seconds to draw a page. Past either limit it kills the worker's whole process group. An error comes back as a short code, such as `password` or `macros`, which `sandbox` turns into the message shown to the admin.

The worker's modes are `inspect`, `render` and `stamp` for PDFs, `stamp-pdf` for a PDF download, `inspect-xlsx` and `stamp-xlsx` for workbooks, `inspect-mp4` for videos, `logo` and `icon` for the branding images, and `confinement`, which confines itself and reports how, for `snug doctor`. Workbooks and videos are read with the standard library alone (`snug.rendering.workbook` and `video`). The worker never decodes a video. It reads the headers of the video's boxes and its index, and the investor's browser plays it.

On a Mac, which has no Landlock, `SNUG_UNSAFE_UNCONFINED_RENDERING=true` lets the worker run unconfined for development. `snug check` warns whenever it is on.

## Showing a document

**A PDF** is shown one page at a time. The page image, `/v/<token>/pages/<page>-<width>.webp`, is drawn by a worker at 800 or 1,600 pixels wide and kept in `renders/`. A second worker stamps that investor's watermark on every request: their email address and the date across the page, and a footer band with their address, the date and the link's reference. The browser picks the width from a `srcset` and fetches the next page ahead.

**A download** is made for each request, in a worker. A PDF keeps its text and gets a faint diagonal mark on every page, a line at the foot of each page and the same details in its properties. A workbook gets the details in its document properties, with every sheet left as uploaded. A video is the original file. The audit log records each download with a hash of the exact file sent.

**A workbook** is shown as a card with its description, its sheets and a download button.

**A video** plays in the browser from `/v/<token>/video`, which answers only requests for a range of bytes, at most 8 MB at a time (`snug.core.ranges`). The investor's email address, the date and the link are drawn over the player, and Snug's own full-screen button takes the player and the mark together.

## Reading time

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/diagrams/reading-time-dark.svg">
  <img alt="A sequence of the browser, Snug and the database: showing a page starts a visit and its clock, heartbeats every 15 seconds credit up to 20 seconds each to the page shown, hiding the tab ends the count and showing it resumes the clock without credit, and the People page shades a cell for each page by its time." src="images/diagrams/reading-time-light.svg">
</picture>

`snug.analytics.reading` turns the reader's events into visits and seconds per page:

- Showing a page continues the visit by the same session to the same document within the last 30 minutes, or starts a new one. A new visit is recorded in the audit log, and after a link's first visit Snug emails the admins who have first-open emails turned on (`snug.analytics.notify`). A single conditional update on `Link.first_opened_at` claims the first open, so two pages loading together send one email.
- While the tab is visible, the page's script (`viewer.js`) sends a heartbeat every 15 seconds with `navigator.sendBeacon`. Snug credits the page with the time since its clock last ran, never more than 20 seconds, so a browser cannot claim more time than passed, and a sleeping laptop does not count.
- Hiding or closing the tab sends `end`, credited the same way. Showing it again sends `resume`, which restarts the clock and credits nothing.
- A video's script (`video.js`) sends `part` as it plays into each 10-second part, and each part counts as a page. A workbook's card counts as its one page.

`snug.analytics.summary` adds the seconds up for a document's page and for People, and `snug.analytics.charts` draws the strips and bar charts as SVG on the server, with the colour of each cell set by a class for its band of time. The pages need no charting script.

`snug housekeeping` deletes reading data a set time after its link is revoked or expires (12 months unless another period is set in Settings), and records how many visits it deleted.

## The audit log

`snug.audit.models.record` writes an `AuditEvent`: when, who (an admin, an investor's email, the command line or the system), the action, the address, the browser, the country and any details as JSON. Each app wraps it in its own `_audit` helper. Actions are named by area: `admin.login`, `document.version_uploaded`, `link.revoked`, `viewer.downloaded`, `settings.email_changed`, `system.reading_data_deleted` and so on.

The Audit log page filters by kind or email address. Each CSV export is recorded in the audit log before the rows stream, and any cell a spreadsheet would run as a formula gets a leading quote.

## Email

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/diagrams/email-dark.svg">
  <img alt="Snug tries each route in order and uses the first that applies: Google Workspace saved in Settings, SMTP saved in Settings, SMTP in snug.env, the outbox folder on localhost, or no email. For every SMTP send, Snug checks that the server resolves only to public addresses, then connects to the address it checked." src="images/diagrams/email-light.svg">
</picture>

`snug.core.mail.outgoing` picks the route for each message, and `snug.core.email_backend.EmailBackend` sends it. For SMTP it resolves the server's name, refuses it unless every address is public, then connects to the address it checked while TLS still checks the certificate against the name, so a DNS change between the check and the connection cannot redirect it. For Google it calls the Gmail API (`snug.core.gmail`) with an access token from the stored refresh token, kept in memory for its hour.

Connecting Google is an OAuth flow with PKCE, started from Settings. The OAuth client belongs to the founder's own Google Cloud project, and the mailbox's user approves it only to send email and to see the mailbox's address. Snug keeps the approval in the session until the admin confirms it with a fresh authenticator code.

First-open notices are sent from a background thread, so a slow mail server does not slow the page. Forgotten-password links are too, so that the reply takes as long whether or not the address is an admin's. An investor's code is sent while they wait, unless their link has had too many wrong addresses: then it is sent in the background, so the reply cannot show whether one went. Snug records whether each send worked, and the Overview and `snug doctor` show it.

Snug makes HTTP calls to Google and DB-IP alone, through `snug.core.outbound.urlopen`, apart from `snug doctor`, and housekeeping every hour, fetching `/healthz` from Snug's own domain. It takes proxy settings from the environment only. On macOS, urllib's default looks them up through system frameworks that kill a process gunicorn has forked.

## Background work

`snug housekeeping` runs every hour, started by the thread in gunicorn's master. In order, it:

1. deletes reading data past its retention period;
2. deletes expired codes, viewer sessions, setup links and rate-limit windows;
3. records that housekeeping ran;
4. runs the slower `snug doctor` checks (the document worker's confinement, DNS and HTTPS) and stores their results for the Overview;
5. fetches DB-IP's country data when a new month's is out, into `geoip/countries.sqlite3`, replacing the old file only once the new one is complete.

`snug doctor` checks housekeeping, the backups, the document worker, DNS, HTTPS and the certificate, email and the country data, and can send a test email. The Overview shows any problem from the same checks. The quick ones run on each visit; the slower ones come from what housekeeping stored.

## Backups and restore

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/diagrams/backups-dark.svg">
  <img alt="Litestream copies the database into a replica on the same volume. Every 5 minutes, rclone checks the bucket belongs to this Snug, then syncs the replica, the uploads and the branding through rclone crypt to the founder's bucket, keeping anything it would overwrite or delete for 30 days. A restore moves the current data aside, copies everything back, and has Litestream rebuild the database at any moment in the last 7 days." src="images/diagrams/backups-light.svg">
</picture>

Litestream ([`deploy/litestream.yml`](../deploy/litestream.yml)) follows the database's write-ahead log and writes a replica to `backup/database`, with a snapshot each day kept for 7 days. The backup container ([`deploy/backup.sh`](../deploy/backup.sh)) then copies that replica, `originals/` and `branding/` to the bucket every 5 minutes through an rclone `crypt` remote, so the files are encrypted before they leave the server.

Before each copy, the backup container checks an `instance` file in the bucket against its own. A bucket holding another Snug's backups is never written to, so a new server started before its restore cannot overwrite them. Each run writes `backup/last-success` or `backup/problem`, which `snug doctor` reads.

`sudo snug restore` ([`deploy/restore.sh`](../deploy/restore.sh)) stops Snug and both backup containers, moves the current data into `previous-<time>/`, copies everything back from the bucket, and has Litestream rebuild the database, as it is now or at a moment given with `--at`. [docs/backups.md](backups.md) has the details.

## The front end

Every page is rendered on the server from Django templates, with one stylesheet, `snug/static/snug/css/snug.css`, built on the tokens in [docs/design/snug.css](design/snug.css). The fonts (Geist and Geist Mono) and the icons (Phosphor, through the `{% icon %}` template tag) are served by Snug itself, so a page loads nothing from anywhere else.

The content security policy allows no inline script or style, so the few scripts are files, each wired with `addEventListener`, and every page works without them:

| Script | What it does |
|---|---|
| `theme-init.js` | Applies the saved light or dark theme before the page is drawn |
| `theme.js` | The theme toggle |
| `admin.js` | Admin helpers: upload progress, copy buttons, and the tabs on a narrow screen. It sends an upload form itself, and the server answers with the address to go to, since a followed redirect would lose its `#section`. |
| `reveal.js` | The eye that shows what is typed in a password box |
| `nda-first.js` | Shows the box for writing the first NDA while a box asking for the NDA is ticked |
| `brand.js` | Keeps the accent colour's picker and its text in step |
| `viewer.js` | The reader's keyboard and page picker, and heartbeats |
| `video.js` | The video player's heartbeats, parts and full screen |

Investors' pages get the company's accent colour from a small stylesheet, `/brand/brand.css`, generated from Settings.

## Tests

The tests are in `tests/`, run with pytest.

- `test_viewer_urls.py` and `test_admin_urls.py` walk every URL and check that each one's view is guarded.
- A fixture in `tests/conftest.py` stops any test reaching the internet.
- The confinement tests run the real worker under Landlock and seccomp, so they run on Linux only.
- `tests/e2e/` drives Chromium with Playwright through the investor's journey and the admin controls that need a real click.
- `test_diagrams.py` checks the diagrams in this document were rebuilt after their sources changed.

[CONTRIBUTING.md](../CONTRIBUTING.md) lists the commands. CI runs the same checks on every pull request (`.github/workflows/ci.yml`), along with CodeQL, and `image.yml` builds the container image, deploys it with Compose and checks backups and restore end to end before it is published.

## The diagrams

The diagrams are drawn by hand in `docs/diagrams/`, one SVG each, with classes in place of colours. `python3 docs/diagrams/build.py` writes a light and a dark copy of each to `docs/images/diagrams/`, with Snug's colours and the Geist fonts inside, and the documents show the one that matches the reader's theme.
