# Snug: project brief

Snug is a self-hosted data room for early-stage founders. A founder runs their own instance on a single cloud server and uses it to share a pitch deck or a due-diligence folder with named investors. Each investor gets their own link. The founder can see who opened what, page by page, and can revoke access at any time. Every page the investor sees carries their email address as a watermark.

Snug is published by Negroni Venture Studios as open source. Negroni does not host it. Each founder runs and owns their own instance, and Negroni never holds their data.

This brief describes the first version. Read it in full before planning, then propose a plan and a repository layout before writing code.

## Why we are building it

Founders raising a seed or Series A round usually share material in one of two ways:

- A Google Drive or Dropbox folder. This is free and familiar, but it gives no reading analytics, and folders shared with "anyone with the link" leak.
- A hosted tool such as DocSend or Papermark. These add per-viewer links, page analytics, NDA gating and watermarking, but cost money each month and put the founder's most sensitive documents on a third party's servers.

Snug covers the useful middle: the features founders actually use from the hosted tools, on infrastructure the founder controls, ideally in a UK region.

Papermark is the closest comparison. It has had four published CVEs since 2025, all in access-control or file-handling code of exactly the kind Snug needs. Correct authorisation is therefore the most important property of this project, ahead of features.

## Users

- **Founder (admin).** Uploads documents, creates rooms, issues links, reads analytics. Technically capable but busy, and may be in the middle of a raise when something breaks. A small company may have two or three admins; all admins have equal rights.
- **Viewer (investor, adviser, partner).** Receives a link by email, verifies their address, accepts an NDA if one is required, reads documents in the browser. Must not need an account or any software beyond a browser.
- **Operator.** Usually the founder again. Creates the server, sets up DNS and email, and is responsible for updates. The deployment must assume this person will not log in to the server again unless something goes wrong.

## Scope of version 1

### Documents

- PDF only. Founders export decks, spreadsheets and drawings to PDF themselves. Say so clearly in the documentation.
- A document can have new versions uploaded. Links point at the document and always show the latest version. Analytics record which version was viewed.
- Configurable maximum upload size, defaulting to something sensible for large decks.

### Rooms

- A room is a named collection of documents, with at most one level of folders.
- A single document can also be shared on its own without a room.
- Each room can optionally require acceptance of an NDA before anything inside it is shown.

### Links

- A link is issued to one named email address for one document or one room.
- Each link has: an unguessable token (at least 128 bits of randomness), an optional expiry, a revoked flag, whether email verification is required (default on), whether downloads are allowed (default off), and an optional note for the founder's own use (for example "Partner at X Ventures").
- Revoking a link takes effect immediately, including for any session already open.
- The founder can copy the link to send it themselves, or have Snug send it by email.

### Viewer verification

- When verification is required, the viewer enters their email address and Snug emails a six-digit one-time code.
- Codes are single use, expire quickly and are rate limited per link and per IP address.
- A verified session is bound to the link and the email address, stored in a secure, HTTP-only cookie, and has a configurable lifetime.
- The email entered must match the address the link was issued to. A forwarded link is useless to anyone who cannot read the original recipient's inbox.

### NDA click-through

- Each room can have NDA text set by the founder. The text is versioned.
- Before seeing the room, the viewer enters their name and ticks to accept.
- Snug records the name, email, link, timestamp, IP address, user agent and a hash of the exact NDA text accepted.
- This is a record of acceptance, not an electronic signature. The documentation must say so.

### Viewer and watermarking

- Pages are rendered on the server to images and served one at a time. The original PDF is never sent to the browser unless downloads are allowed on that link.
- Each served page is stamped with a watermark showing the viewer's email address, the date and a short link identifier, so a leaked screenshot can be traced.
- Rendered pages are cached on disk per document version, page and link. The cache can be deleted at any time and rebuilt.
- When downloads are allowed, the downloaded file is a watermarked PDF built from the same rendered pages.
- The viewer page uses no third-party scripts, fonts or CDNs. Everything is served from the instance.
- Known trade-off: text in rendered pages cannot be selected or searched. Accept this for version 1.

### Analytics

- The viewer page sends events: view started, page shown, a heartbeat while the tab is visible, view ended.
- From these, compute time spent on each page per viewer, number of visits, and first and last seen.
- The founder's dashboard shows, per document: who has opened it, how far they got, and time per page. Per viewer: everything they have looked at across the instance.
- Optional email notification to admins the first time a link is opened.
- No analytics or telemetry are sent anywhere outside the instance. Snug does not phone home.

### Audit log

- An append-only log of admin actions (uploads, link creation, revocation, settings changes, admin logins) and viewer access (verification, NDA acceptance, document views, downloads).
- Viewable and exportable as CSV from the admin interface.

### UK-only mode (optional, per link or per room)

- Restrict viewing to IP addresses that geolocate to the United Kingdom, using a local GeoIP database file. No external lookup service.
- Record the country of every view in the audit log whether or not the restriction is on.
- The documentation must state plainly that this can be bypassed with a VPN and is not, on its own, an export-control measure.

### Admin interface

- Server-rendered HTML with minimal JavaScript.
- Admin login by email and password (argon2id) plus a mandatory TOTP second factor.
- The first admin is created from the command line on the server. Further admins are invited from the interface.
- The founder can set a logo and an accent colour, shown on viewer pages. Snug does not show Negroni branding on viewer pages.

## Out of scope for version 1

- Hosting Snug as a multi-tenant service. One instance serves one company.
- Rendering Office, CAD, video or image files.
- Q&A threads, comments or annotations.
- Electronic signatures.
- Single sign-on.
- AI features of any kind.
- Investor update emails or newsletters.

## Security requirements

These are requirements, not suggestions.

1. **One authorisation path.** Every request for viewer content goes through a single function that checks, in order: the link exists, is not revoked, has not expired, the session is valid and bound to this link and email, the NDA has been accepted if required, the geographic restriction is met if set, and the requested document belongs to the link's document or room. No viewer endpoint may skip it.
2. **No guessable identifiers in URLs.** Database primary keys never appear in viewer-facing URLs. Use random tokens.
3. **Tests for every authorisation rule.** Write a test matrix covering each check above, including revoked links with open sessions, expired links, a valid session for link A used against link B, and a document outside the link's room. Include regression tests for the classes of bug found in Papermark: path traversal in file serving, unauthenticated uploads, permissive CORS, and one admin action affecting another resource it should not reach.
4. **No CORS.** Snug serves its own pages only and sends no CORS headers.
5. **Strict Content Security Policy** on all pages, with no inline scripts.
6. **CSRF protection** on every state-changing admin request.
7. **Rate limiting** on login, code requests and code verification.
8. **File handling.** Store uploads under generated names, never the uploaded filename. Resolve every file path from database records, never from request parameters. Render PDFs in a separate process with a timeout and memory limit, so a malformed file cannot take the server down.
9. **Secrets.** Never log tokens, one-time codes, session identifiers or passwords.
10. **Dependencies.** Keep them few and well maintained. Ask before adding one. Check every dependency's licence is compatible with Snug's licence (see below).

## Technical decisions

- **Language:** Python 3.12 or later.
- **Web framework:** a small, mature framework with good session and CSRF support. FastAPI or Starlette with Jinja2 templates is the starting suggestion. Propose an alternative if you think it is better, with reasons.
- **Database:** SQLite, in WAL mode. Migrations must be versioned and run automatically on start.
- **PDF rendering:** pypdfium2. Do not use PyMuPDF, Ghostscript or Poppler: they are AGPL or GPL licensed and incompatible with releasing Snug under MIT.
- **Images and watermarks:** Pillow.
- **Email:** plain SMTP, so any transactional provider (Postmark, Amazon SES, Resend) works with configuration only.
- **GeoIP:** a local country database file. DB-IP's free country database is licensed CC BY 4.0, which requires attribution in the documentation and interface. Check the current terms before choosing.
- **Tooling:** uv for dependencies, ruff for linting and formatting, pytest for tests, type hints throughout.
- **Licence:** MIT. Every dependency must be under a licence compatible with distributing Snug as MIT, which rules out GPL and AGPL dependencies.

## Deployment

The target is a single DigitalOcean droplet, though nothing should depend on DigitalOcean specifically.

- Snug ships as one container image, published to GitHub Container Registry at `ghcr.io/negroni-venture-studios/snug`.
- A Docker Compose file runs three services: Snug, Caddy (which obtains and renews TLS certificates automatically) and Litestream (which replicates the SQLite database continuously to an S3-compatible bucket).
- Uploaded originals are also copied to the bucket. The render cache is not backed up.
- The database and files live on the droplet's own disk. The bucket is for backup and recovery only, never read during normal operation.
- A cloud-init file sets up a fresh Ubuntu LTS droplet: installs Docker, enables unattended security upgrades, configures the firewall for ports 22, 80 and 443, and starts the stack.
- All configuration comes from a single environment file with documented settings.
- A `snug doctor` command checks that DNS points at the server, TLS is working, a test email can be sent and received, and the latest backup is recent. The deployment guide asks the operator to run it before sharing any link.
- A `snug restore` command rebuilds an instance from the bucket onto a fresh server. Document it and test it.

## Documentation

- `README.md`: what Snug is, who it is for, what it does not do, and a short quick start.
- `docs/deploy-digitalocean.md`: step-by-step setup on a droplet in the London region, written for a founder who has used a terminal but has never run a server.
- `docs/email.md`: setting up a transactional email provider on the founder's own domain, including SPF, DKIM and DMARC. Most failed setups will fail here, so be thorough.
- `docs/security.md`: what Snug protects against and what it does not. Watermarks and disabled downloads deter casual leaking but cannot stop someone photographing a screen. UK-only mode is not export control.
- `SECURITY.md`: how to report a vulnerability, and the commitment to respond.
- British spelling throughout documentation and interface text.

## Suggested order of work

1. Project skeleton, configuration, database and migrations, admin creation from the command line, admin login with TOTP.
2. PDF upload, versioning and rendering in a separate process.
3. Links, viewer verification by emailed code, the single authorisation path and its full test matrix.
4. The viewer, watermarking and the render cache.
5. Analytics events and the founder's dashboard.
6. Rooms, folders, NDA acceptance and the audit log.
7. Container image, Compose file, Caddy, Litestream, cloud-init, `snug doctor` and `snug restore`.
8. UK-only mode and first-open notifications.
9. Documentation, then a full deployment to a real droplet from the written guide alone.

Commit in small steps. Each step should leave the test suite passing.

## Open questions

Raise these with Alasdair rather than deciding them silently:

- Whether the default session lifetime for viewers should be hours or days.
- Whether founders want a weekly digest of viewing activity by email in addition to first-open notifications.
- Whether documents should support a cover page or an introductory message shown before the first page.
