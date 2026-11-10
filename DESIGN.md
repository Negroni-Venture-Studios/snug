# Snug: design

What Snug is and why it is built the way it is; [docs/architecture.md](docs/architecture.md) explains how the code works. The original brief is kept in [docs/BRIEF.md](docs/BRIEF.md); where the two disagree, follow this file.

Sections 1 to 3 describe Snug as built, except the features that section 4 lists as not built yet.

## 1. Decisions

| Area | Decision | Why |
|---|---|---|
| Web framework | Django 6.1, moving to 6.2 LTS when it ships (April 2027, supported to April 2030) | CSRF, server-side sessions, versioned migrations, argon2id, CSP and security headers are built in and long audited. Papermark's CVEs were in its hand-written security primitives. Fewer dependencies than the Starlette path. |
| Django admin | `django.contrib.admin` is not installed | Extra attack surface, and it would not enforce TOTP. |
| Audit log | Every admin and viewer event, in an append-only table, with a filtered view and a CSV export in the admin interface. Every viewer event records the visitor's country from DB-IP's data, whether or not the link is UK-only, shown beside the address; empty when the address cannot be placed. | Each sign-in and access is recorded from the first. |
| Lost second factor | One-time recovery codes issued at enrolment; replacing your own authenticator in Settings, with the password and a code from the old one or a recovery code; any admin can reset another admin's second factor; `snug reset-2fa` on the server | A lost phone must not lock an admin out for good. |
| Forgotten password | A "Forgotten your password?" link on the sign-in page emails a one-time link that works for one hour. The reply is the same whether or not the address belongs to an admin, and the link is created and emailed in the background, so the reply does the same work, and takes as long, either way. Limited to 5 requests per IP address and 3 per email address in 15 minutes. The page that sets the new password also asks for a code from the authenticator, or a recovery code, with its own limit of 5 codes per admin in 15 minutes, so someone holding a link cannot use up the admin's sign-in attempts and lock them out. Sharing one allowance with sign-in would bring that lockout back; keeping this one to a single link's worth means someone with both the password and the mailbox gets 15 guesses in 15 minutes rather than 10, and every wrong code is audited. A bad password spends no code; a wrong code leaves the link working, and the fifth spends it. The count is kept on the link itself, so it never resets while the link works. Changing the password signs the admin out everywhere and emails them; the request, failures and the change are audited. Without email, the page has instructions to ask another admin or run `snug reset-password` on the server, an audited command that replaces Django's unaudited `changepassword`. | People forget passwords. With the second factor asked for on the reset page, someone in the admin's mailbox can neither sign in nor change the password. |
| Changing your password | In Settings, with the current password, the new one twice and a fresh code from the authenticator, under the same 10-in-15-minutes limit as confirming admin changes. The new password is checked first, so a weak one spends no code. This browser stays signed in; every other session ends. The admin is emailed and the change is audited. | The same proof as any other change to an admin account, so someone at an unlocked computer cannot change it. Signing out elsewhere ends any session a thief may hold. |
| Replacing your authenticator | In Settings, with the password and a code from the current authenticator or a recovery code, under the 10-in-15-minutes confirmation limit. The old authenticator and recovery codes stop working at once, which ends every session. The admin goes straight to the setup page for the new one, and its link is emailed too, working for 72 hours. Every admin is emailed and the change is audited. | A new phone should not need another admin. The emailed link means closing the tab halfway does not lock anyone out, and the alert means every admin hears of a takeover. |
| Removing admins | An admin cannot delete themselves, and the last admin cannot be deleted | Avoids locking a company out of its own instance. |
| Second factor | TOTP with a one-step window either side and replay protection (the last used step is stored). Codes must be ASCII digits. Ten recovery codes of 80 bits each, stored as SHA-256 hashes. TOTP keys stored unencrypted. | The window covers clock drift; replay protection stops a shoulder-surfed code being used twice. TOTP keys must be readable to check codes; the database stays on the founder's server, and backups are encrypted. Encrypting them would need a new dependency, its key would sit on the same server, and losing that key would wipe every admin's second factor. A stolen database copy still needs each admin's password. |
| Sign-in limits | Per address: 20 password, 20 code and 10 recovery attempts. Per account: 30 password attempts, 10 code attempts and 5 recovery attempts. All in 15-minute windows, held in SQLite so every process shares them. Each attempt is counted before it is checked (`ratelimit.allow`), so simultaneous requests cannot slip under a limit. 5 wrong codes after one correct password returns to the password step. | A low account limit would let anyone lock a founder out by typing wrong passwords, and a correct password only reaches the code step. |
| Admin sessions | A new session id at each step. Five minutes from password to code, and back to the password if the password or TOTP key changes in between. Ending after one hour idle or twelve hours absolute. The session hash covers the password and the TOTP key, so changing either signs out everywhere. | A new id at each step stops session fixation, and a new password or key ends every session without scanning the session table. |
| File handling | Uploads stream to a temporary file in the data folder, with a hash and a size limit for their kind, the kind decided by the first bytes (`%PDF-`, a zip, or an `ftyp` box), then an inspection in the worker, before a rename into `originals/<random>.pdf`, `.xlsx` or `.mp4`. The uploaded name is kept for display only. | The file-handling rule in the security requirements: generated names, and paths from records only. |
| Render worker | `python -I -m snug.rendering.worker`, with a clean environment (no `SNUG_*`), given the file as an open file descriptor, never a path (it reads PDFs, checks and stamps workbooks, and checks videos, reading a video in pieces), and returning results through a pipe, which Snug reads in bounded chunks. Past 64 MB (for a stamped download, twice the upload limit or the file's size, whichever is larger, plus 1 MB) or the deadline, the worker's whole process group is killed. It limits itself to 1 GiB of memory (enforced on Linux), 60 s CPU, 64 MB written to any file and 32 open files, then confines itself with Linux Landlock (no filesystem access except reading system fonts, no TCP) and a seccomp filter that refuses to create any socket, since Landlock's network rules cover TCP only. Both are required: Landlock ABI 4 (Linux 6.7) or later and the filter. If either fails, the worker refuses to read the file, and the upload fails with "This server cannot open documents safely". `SNUG_UNSAFE_UNCONFINED_RENDERING=true` allows unconfined reading for development on a machine without Landlock, such as macOS; Snug's system check warns whenever it is on. Timeouts are 60 s to inspect an upload or stamp a download, and 30 s to render or stamp a page. At most 2,000 pages; renders capped at 6.4 megapixels and 8,000 px a side. Snug's own processes (gunicorn and the `snug` command) mark themselves non-dumpable, so a compromised worker cannot read their `/proc` entries or environment. The worker runs as the same user as Snug and relies on Landlock and the limits above. Switching to a separate user would need Snug to hold the power to switch to any user, root included, which a compromised Snug could use. `snug doctor` reports the confinement level, from a fresh worker that confines itself and reads nothing. | A malformed or hostile PDF cannot take the server down. Even if it took over PDFium, it could not read the secret key, the database or other documents. |
| Render cache | Plain pages per version, page and width (800 or 1,600 px), as WebP, in `renders/`. Watermarks are stamped on every request. | One cached page serves every viewer, and the cache can be deleted and rebuilt. The watermark has the date, so a per-link cache would go stale daily and keep a copy of every deck per investor. |
| Audit log storage | Append-only, refused by the model, its queryset and SQLite triggers | The triggers stop even raw SQL from inside Snug changing or deleting an entry. |
| Admin invites | An emailed setup link: 256-bit token, stored as a hash, one live link per admin, working once for 72 hours. The invitee chooses a password and adds the authenticator in the browser, then sees their recovery codes once. Without email, admins are created with `snug createadmin`. | No password ever travels by chat. 72 hours spans a weekend without leaving a forgotten invite open. |
| Confirming admin changes | Inviting, removing or resetting an admin asks for a fresh code from the acting admin's authenticator, used once (10 tries per admin in 15 minutes). | Someone at an unlocked laptop, or with a stolen session, cannot quietly add themselves. |
| Admin change alerts | Every active admin is emailed when an admin is invited, removed or has their second factor reset. | No admin can be added without every other admin hearing of it. |
| Rate limiting | A small database-backed limiter of our own, not a library | Shared correctly across server processes; easy to test. |
| Client IP | `X-Forwarded-For` is trusted only from Caddy, which has fixed addresses on the Compose network; the Compose file sets `SNUG_TRUSTED_PROXIES` to them. IPv6 is on for that network. | Rate limits, UK-only mode and the audit log all depend on the real address. Without IPv6 on the network, Docker relays IPv6 visitors through its own proxy, and they would all share one address. |
| Verification codes | Six digits, single use, ten minutes, at most five wrong attempts per code, rate limited per link, per IP and per email address. One live code per link: asking again replaces it, so checks against a live code are also capped at 50 per link per day (checks with no live code are not counted, so they cannot lock the owner out). Each try is claimed before the code is compared, so simultaneous guesses cannot exceed five. Stored only as an HMAC (keyed with the secret key) over the link's current token hash, so reissuing a link ends its code. | Without a cap per code, one code could be guessed without limit. A six-digit code hashed without a key could be recovered from a database copy in a second. |
| Viewer session lifetime | 7 days by default, set with `SNUG_VIEWER_SESSION_DAYS` (1 to 90) | Revocation is checked on every request, so a long session does not weaken it. |
| Viewer sessions | Separate from admin sessions. One `__Host-snug-viewer` cookie (HttpOnly, SameSite=Lax) holds 256 random bits; the database keeps only their hash, with one row per link the browser has opened, bound to the link, its current token and its email address, so reissuing a link ends every session for it. The cookie is replaced each time the viewer verifies. | Each link is verified and revoked on its own, and a database copy opens nothing. With Lax, a viewer arriving from a link in an email is recognised; Strict would drop the cookie on that first request. |
| Viewer rate limits | Asking for a code: 20 attempts per IP in 15 minutes; 10 wrong addresses per link in an hour, after which every request on that link gets the same neutral reply for the rest of the hour (the right address still gets a code); 5 codes sent per link in 15 minutes; 10 codes to one inbox in an hour. Entering a code: 30 per IP in 15 minutes, five per code, and 50 per link a day. Opening a link without verification: 30 per IP in 15 minutes. Downloads: 30 per link an hour, since each stamps the whole PDF in a worker. Reading-time heartbeats: 600 per IP in 15 minutes, about ten open tabs. Each request is counted before its limit is checked (`ratelimit.allow`), so simultaneous requests cannot slip under. | The per-inbox limit stops Snug being used to flood someone's email. |
| Viewer URLs | Everything a viewer sees is under `/v/<token>`; a room link's documents are under `/v/<token>/d/<document token>`, and room membership is checked on every request, so removing a document from a room ends access to it at once. Each view is wrapped by `viewer_content` (which calls `authorise()`), by `viewer_nda` on the NDA page (`authorise()` without its NDA check) or, for the pages that start a session, `viewer_gate` (the link check, and the UK-only check, so nobody abroad is sent a code). A test walks the URL configuration and fails on any viewer view without one, on any integer parameter other than a page number or image width (positions within the document the token grants, not identifiers), on any path parameter, and on any route outside `admin/` and `v/` other than the root redirect and the founder's brand files under `brand/`. | A test enforces the single authorisation path. |
| Wrong email address | On the landing page, a wrong address gets a message that it is not the one the link was sent to, and no code is sent. After 10 wrong addresses on a link in an hour, it switches to a neutral reply for every request, so a guesser learns nothing and nobody can lock the owner out. In that mode the code is emailed in the background, so the reply's timing does not depend on the address, and a wrong code says only that it is not right, never how many tries are left. | Typos are the common case. Guessing is rate limited, and a code still needs the real inbox. |
| Links without verification | The link opens a short page with a button to open the document or room, which starts the session; nothing happens on a plain visit. | Email security scanners fetch links before people do. A visit must not start a session or count as a first open. |
| Watermark stamping | In a fresh confined worker on every request, like rendering: Snug passes the cached page and the font as open files, and checks only the container format of what comes back. The mark is the viewer's email address and the date, tiled diagonally at about 28% opacity, plus a footer band reading "Shared with *email*", the date and "Link *ref*". | The cached page came out of a worker that reads untrusted PDFs. Decoding it in Snug's own process would let a compromised worker attack Snug's image decoder (libwebp had exactly such a bug, CVE-2023-4863). |
| Reading time | A page is recorded as shown when the reader serves it. While the tab is visible, the reader sends a heartbeat every 15 seconds; "end" when the tab is hidden or closed, "resume" when it is shown again. Snug credits each heartbeat with the time elapsed since the last, at most 20 seconds, to the page being shown. A visit ends after 30 minutes with no events. Each new visit is audited as `viewer.document_viewed`. | The server measures time, so a browser cannot inflate it, and a sleeping laptop or a hidden tab does not count as reading. |
| NDA | Available on single-document links as well as rooms. One NDA for the whole instance, set in Settings and versioned; a room, or a link to a single document, requires it or not. Acceptance is per link and per version: editing the NDA asks everyone to accept the new version on their next visit, and earlier acceptances stay in the history. Each acceptance records the name, email, link, time, IP address, user agent and a SHA-256 of the exact text. | A document shared on its own may need an NDA too. Most founders have one NDA. Every record matches the exact words that person agreed to, and is tied to the link it was made on, as the brief asks. |
| UK-only mode | A link to a single document, or a room (and so every link to it), can open only for visitors in GB, Jersey, Guernsey and the Isle of Man (decided 6 November 2026), by their address in DB-IP's data. It is `authorise()`'s check 4; the gate pages check it too, so nobody abroad is sent a code, and a visitor without a session is checked before being asked to verify. It fails closed: an unknown country, or a server without the data, is refused. A refusal shows one page (403) and is audited as `viewer.outside_uk` with the country. `snug doctor` and the Overview warn when something is UK-only and the data is missing or older than last month. The link form and the room's switch include a credit to DB-IP. | Many UK funds and family offices sit in the Channel Islands. Failing open would let anyone through whenever the data was missing. A VPN gets round it, as the form's hint says and `docs/security.md` will. |
| Rooms | A named collection of documents with at most one level of folders. A document can be in any number of rooms, and a link points to one document or one room. | A document is uploaded once and keeps one version history and one set of analytics, whichever rooms it is in. |
| Cover message | Optional plain text on a link, up to 1,000 characters, with line breaks kept and nothing rendered as HTML. It is at the top of the viewer's *About this link* panel on every page, and appears in the email with the link. | Cheap, and founders asked for it. Plain text means a message cannot contain script or a disguised link. |
| Email settings | Saved in Settings (`EmailSettings`, apart from `SiteSettings`, which every viewer page reads), and used from the next email, with no restart. Without them, the `SNUG_SMTP_` settings in `snug.env` are used, then the outbox on localhost. The transport (`snug.core.email_backend`) reads the settings in force when each message is sent; a message sent from a background thread is given the settings its request read, so the thread need not look them up; it does record whether the send worked, and closes its database connection when it finishes. Passwords are stored as they are and never shown again. A saved one shows as twelve dots, the field's placeholder, so its value never reaches the browser and an empty box keeps it. Saving needs a fresh authenticator code (the 10-in-15-minutes confirmation limit), and every admin is alerted through the settings in force before the change, so a stolen session cannot quietly reroute codes and reset links. The SMTP server must be on port 25, 465, 587, 2465, 2525 or 2587 (2465 and 2587 are Amazon SES's, for DigitalOcean) and resolve only to public addresses (any, on localhost), with IPv6 ranges that contain an IPv4 address judged by the IPv4 address inside (IPv4-mapped) or refused (NAT64, 6to4, Teredo), so Snug cannot be aimed at its own network or the cloud metadata address. The check runs when saving, only after the code is confirmed, so someone with a session but no code learns nothing about internal names; and again when sending, when Snug connects to the very address it checked, while TLS still verifies the certificate against the server's name (DNS rebinding cannot slip in between). Test sends are limited to 5 per admin in 15 minutes. `snug setup` does not ask about email. The panel offers Google Workspace or another provider over SMTP, the fields for each shown by CSS alone (`:has()`). The setup is laid out as numbered steps, all on the page from the start and greyed until the one before is done (the client or SMTP server; for Google, the sending mailbox; a test), so the next thing to do is always on the page. Each save, connection or error lands on the step to do next, with its notice there; Google's refusals are held in the session over a redirect to the mailbox step, so its one-time code leaves the address bar. *Clear email settings*, in the same panel as the choice of provider and under it, shown whenever anything is saved (working or not), forgets everything saved here (the client, the mailbox's permission, the SMTP details) with a fresh code, alerts every admin through the route in force before, then revokes at Google; email falls back to `snug.env`, the outbox on localhost, or off. Google Workspace sends over the Gmail API on port 443 (decided 9 November 2026), since DigitalOcean blocks the only ports Google's mail servers accept, and Google has offered no way to create an OAuth client from the command line since March 2026. The founder makes an internal OAuth client in their own Google Cloud project ([docs/email-google.md](docs/email-google.md)), and the sending mailbox approves it for `gmail.send` alone (plus `openid email`, for its address), with PKCE. *Connect Google* is a link, since the content security policy's `form-action` would stop a form's redirect to Google; the approval is held in the session until a fresh authenticator code confirms it, then every admin is alerted through the settings in force before. The mailbox step has a note, before the trip to Google, that this second code is coming, and on the return a green notice shows the approved mailbox. Snug keeps the refresh token; access tokens are kept in memory for their hour. *Disconnect* revokes the permission at Google, and needs a fresh code too, since it turns email off. Every save, connection and disconnection compares everything that decides delivery before and after (the route, server, port, account, address, and the secrets compared in memory in constant time, since another account's password at the same server reroutes mail too), and if it changed, alerts every admin through the route before; a permission given up at Google is revoked only after that alert is out. Saving SMTP settings revokes and forgets any Google permission. Google's `code` and `state` are redacted from any logged address. An optional From address lets Snug send as another of the mailbox's addresses, such as one on a Workspace user alias domain, since Google approves the primary account; Gmail honours it only if the mailbox has it under *Send mail as*. Standard library only. Calls to Google, like DB-IP's downloads, go through `snug.core.outbound`, which takes proxy settings from the environment alone. On macOS, urllib's lookup of the system's settings runs Apple frameworks that kill a process gunicorn has forked. | Founders expect to set email up in the interface, and changing it should not need a server and a restart. |
| Weekly digest | Not in version 1 | Needs a scheduler and more email design; first-open notifications cover the urgent case. |
| Backups | Litestream writes the database's replica to a folder on the server; an rclone sidecar copies that folder, the uploaded originals and the branding images to the founder's bucket every 5 minutes, through an rclone `crypt` remote (XSalsa20 and Poly1305, encrypted before upload). The crypt password and salt are generated during setup, and the founder must store them. The database can be restored to any moment in the last 7 days; anything the copy would delete or overwrite in the bucket, the database replica's files included, is kept there under `deleted/<date>/` for 30 days, so the backups survive a wiped server. The sidecar records its last success in the data volume for `snug doctor`. | Litestream only replicates SQLite. The backups go to the founder's own bucket, encrypted so that only the founder can read them. Litestream 0.5 dropped its age encryption and still has no client-side encryption of its own (checked 6 November 2026); rclone's crypt covers everything with one key, at the cost of up to 5 minutes' data rather than a second's. |
| GeoIP | DB-IP IP to Country Lite (CC BY 4.0, checked 5 November 2026), as CSV, which housekeeping downloads each month (or last month's, early in a month before the new one is out) and loads into `geoip/countries.sqlite3` in the data folder: a table of ranges keyed by 16-byte addresses, IPv4 mapped into IPv6, so a lookup is one indexed query. Every row is checked as it streams in, in batches, and the new file replaces the old only once it is complete; if anything is wrong, the data in use stays, and the next try waits six hours. It is not backed up, since it can be fetched again. No new dependency: the standard library's `csv`, `gzip` and `sqlite3`, fetched through `snug.core.outbound`. Attribution in the docs and the admin interface. | Bundling it in the image would go stale. The monthly fetch is documented, and no address is ever sent anywhere. Streaming keeps the load to about 12 MB of memory; building the whole table in memory took about 420 MB, too much beside Snug on a 1 GB droplet. |
| `snug doctor` email check | Sends a code and asks the operator to type it back | Snug cannot read the operator's inbox. |
| Periodic work | `snug housekeeping`, run every hour by a thread in gunicorn's master process, each time as a fresh process. It deletes reading data past its retention period (audited as `system.reading_data_deleted`), and expired codes, viewer sessions, setup links and rate-limit windows; then it runs the slower `snug doctor` checks and stores them for the Overview, and fetches the GeoIP data when a new month's is out. It reports counts only. | The container has no cron. A fresh process keeps the master free of Django, and a failure there never touches the processes serving requests. |
| Coverage | A floor enforced in CI; no Codecov for now | One fewer third-party service and token. |
| CI actions | Every third-party action pinned to a commit SHA, kept current by Dependabot | Supply-chain hardening. |
| Changelog | A plain `CHANGELOG.md` with an `[Unreleased]` section | Changelog fragments in a `changelog.d/` folder solve a throughput problem this project does not have. |
| Security headers | Strict CSP from `default-src 'none'`; `Referrer-Policy: same-origin`; `__Host-` cookies; HSTS for one year, without subdomains or preload; Permissions-Policy and Cross-Origin-Resource-Policy | Viewer URLs contain tokens, so no Referer may reach another site. `no-referrer` would make browsers send `Origin: null` on form posts, which fails CSRF. Subdomains and preload are left to the founder. |
| Logging | No request access log, in Snug or in gunicorn | Viewer URLs contain tokens. |
| Health check | `/healthz` is answered before the host check | Docker's health check probes the container by its internal address, which the host check would refuse. |
| Commit trailer | `Co-Authored-By: Claude <noreply@anthropic.invalid>` | The `.com` address resolves to an unrelated GitHub account. |
| Branding | Snug takes its palette and inspiration from the Negroni brand kit, without following it to the letter. No commercial brand fonts; Geist and Geist Mono (OFL) instead. | PP Hatton and NB Grotesk Pro Mono need commercial licences and cannot ship in an MIT repository or a public image. |
| Front end | Section 3: the Negroni palette, Geist type, Phosphor icons and the reading heatmap, in one stylesheet, `snug.css`, grown from the mockups' `docs/design/snug.css` | Close to the Negroni brand, in one stylesheet with no build step. |
| Logo | The half-glazed snug door (section 3.14) | Door, monogram and Negroni sparkle; a snug is a pub's private room. |
| Link tokens | 256-bit random tokens, stored only as SHA-256 hashes. The full link is shown once, at creation; afterwards the founder can issue a new link, which stops the old one. Each link also has a short, non-secret reference (like `L-7Q2K`) for watermarks and the audit log. | A leaked database or backup exposes no working links. |
| Spreadsheets | Excel workbooks (.xlsx) only, decided 9 November 2026, shared for download only, with no preview, since the working file is what an investor needs and a rough preview would mislead. The kind is decided by the file's first bytes (a zip), and a document's versions are all one kind. The confined worker checks each upload (`snug.rendering.workbook`, standard library only): the root relationship points to the workbook part, whose content type must be a plain workbook's; anything typed as VBA, Excel 4.0 macro sheets, ActiveX or OLE objects, by override or extension default and in any case, is refused, as is any relationship to such code, a part or extension typed twice, part names that repeat or differ only in case, an encrypted part, more than 10,000 parts or more than 1 GiB unpacked by the zip's index. XML is parsed with expat behind handlers that refuse any DOCTYPE or entity declaration, in any encoding. Only visible sheets are listed, to investors and admins. Each download is stamped for its reader in the worker. The comments (above the founder's own) and three custom properties hold the reader's email address, the date and the link, and every other part is streamed through unchanged, so formulas, charts and styles survive. The founder can add a description to a workbook (plain text, up to 1,000 characters, kept from version to version, on every link and in every room), which the investor's card shows above the sheets; a link's cover message is for a note to one investor. The investor sees a card with the sheets and *Download the workbook*; with downloads off, the card has a note that the workbook cannot be opened. The card counts as the workbook's one page, decided 9 November 2026. It sends the reader's heartbeats, so time with it open is measured like time on a PDF page. Downloads are audited with a hash of the file sent. Old .xls files, workbooks with passwords and .xlsm are refused with a plain message. | A financial model is useful only as a working file. Snug cannot burn a mark into one, so the properties and the audit log's hash identify a leaked copy; like the watermark, they cannot stop a determined copier. |
| Videos | MP4 with H.264 video, optionally AAC sound, decided 9 November 2026 as the one combination every browser plays. The confined worker checks each upload without decoding it (`snug.rendering.video`, standard library only): it walks the top-level boxes by their headers, reading only the index whole, so a video larger than its memory never enters it, and refuses QuickTime, other codecs, encrypted tracks, no picture, no length, over four hours and broken boxes. Videos have their own size limit, `SNUG_MAX_VIDEO_MB`, 1 GB by default (1 to 8192); only the two upload forms accept a request that large, and ingest holds each kind to its own once the first bytes show which it is. The investor's browser plays it from Snug alone (`media-src 'self'`), in byte ranges (`snug.core.ranges`), each request authorised like a page image; the stream answers range requests only, at most 8 MB at a time. The admin's preview is served in ranges too, but sends the whole file to a request without one. The investor's email, the date and the link are drawn over the player; the browser's own full screen and picture-in-picture are turned off, and Snug's full-screen button takes the frame, mark and all. Time watched is counted in parts of 10 seconds, each treated as a page, so visits, first-open emails, the reading chart and People work unchanged, labelled by time. A download, when the link allows one, is the original file, audited with its hash. | A demo or a recorded walkthrough is common in a data room. Burning a mark into each viewer's copy would need ffmpeg and minutes of encoding per viewer; the overlay is a deterrent like the page watermark. `docs/security.md` will say what it cannot do: the browser's tools could hide it, Safari on iPhone plays in its own full screen without it, a downloaded video has no mark, and a streamed video can be saved even with downloads off, as the link form's hint says. |
| Downloads | Allowed by default on every link, for every file type; the founder can turn them off on a link that must be view-only. Workbooks and videos are covered in their own rows. A PDF download is the original file, so its text can be selected and searched, stamped in the confined worker with the reader's faint diagonal mark (email and date) on every page, drawn as an image layer so the document's own text stays clean to copy, a line at the foot of every page ("Shared with *email*", the date and the link reference), and the same details in its document properties. The audit log records each download with a hash of the exact file sent. A PDF that opens without a password but is encrypted, with an owner password restricting copying or printing, is stamped from PDFium's unencrypted copy: pypdf cannot decrypt AES without the `cryptography` library, which Snug does not depend on, and the stamped download is written afresh without the restrictions either way. | Investors want the working file, and find view-only rooms frustrating. A footer line alone is easy to overlook, so every page also has the diagonal mark. An image-only PDF cannot be searched or quoted and is many times larger. The stamp, like the watermark, deters casual forwarding and identifies a leaked copy, but cannot stop a determined copier. |
| Trademarks | Logo files are MIT with the code; `TRADEMARKS.md` asks forks shipped as separate products to rename | Keeps "Snug" meaning the Negroni project without heavy policy. |
| Theme | Dark by default; a light, dark and system toggle in every header, cycling in that order | Dark is the Negroni look. Some people read better in light, and system follows their device. |

## 2. Dependencies

| Package | Licence | Purpose |
|---|---|---|
| Django (+ asgiref, sqlparse) | BSD-3-Clause | Framework |
| argon2-cffi (+ argon2-cffi-bindings, cffi, pycparser) | MIT (cffi MIT-0, pycparser BSD-3-Clause) | argon2id |
| pyotp | MIT | TOTP |
| segno | BSD-3-Clause | QR codes for TOTP enrolment, as SVG |
| gunicorn | MIT | WSGI server |
| whitenoise | MIT | Serves static files from the app container |
| pypdfium2 | BSD-3-Clause / Apache-2.0; bundled PDFium includes FreeType, ICU, libjpeg-turbo, lcms, OpenJPEG, libpng, zlib and abseil notices | PDF rendering |
| Pillow | MIT-CMU | Images and watermarks |
| pypdf | BSD-3-Clause | Stamping downloaded PDFs, in the confined worker |
| Geist and Geist Mono fonts (vendored, v1.8.2) | SIL Open Font Licence 1.1 | Interface type |
| Phosphor icons (vendored SVG, core v2.0.8) | MIT | Interface icons |

Development: pytest, pytest-django, pytest-cov, Playwright, ruff, mypy, django-stubs, pre-commit, pip-audit, pip-licenses. CI only: cyclonedx-bom, gitleaks, CodeQL.

Separate services, not linked into Snug: Caddy and Litestream (both Apache-2.0), rclone (MIT).

`THIRD_PARTY_LICENCES.md` ships in the container image, because the licences of the PDFium binary inside pypdfium2's wheel require their notices to ship with it.

## 3. Front end

The design draws on the Negroni brand. It follows the general rules of a design-taste guide (type, colour discipline, copy, button and form contrast, and its list of AI tells) but not its landing-page advice, since Snug is an admin tool.

### 3.1 Design read

An admin tool for founders and a reader for investors, in the Negroni palette, built from native CSS and server-rendered HTML.

On the taste guide's dials: variance 3 (orderly, aligned, few surprises), motion 2 (hover and press feedback only), density 5 in the admin and 2 in the viewer.

### 3.2 Principles

- **The viewer belongs to the founder.** It uses plain neutrals with their accent colour and their logo, and no Snug or Negroni branding. The admin is Snug's own, in the Negroni palette.
- **Server-rendered first.** Every page works as plain HTML forms and links. JavaScript only enhances.
- **The strict CSP shapes the design.** No inline scripts, no `style=""` attributes and no third-party origins (section 3.10).
- **Accessible by default.** WCAG 2.2 AA throughout, with tests where possible.
- **Copy.**
  - British English, plain and low-drama, in sentence case.
  - Form labels sit above their fields, with errors below them.
  - No em or en dashes in interface text, and no middle dots used as separators.
  - Missing values are written in words ("No expiry"), never shown as a dash.
  - Figures come from real data, never invented precision.
  - No exclamation marks and no emoji.

### 3.3 Typography

- **Geist and Geist Mono** (SIL Open Font Licence 1.1), self-hosted as variable WOFF2 files: 70 KB and 72 KB.
  - Pinned to release v1.8.2.
  - Loaded with `font-display: swap`, and the sans is preloaded.
  - The fallback is the system stack, so the page works before the font arrives.
- **Weights follow the brand.** Negroni sets display text in light weights, so page titles and large figures use Geist Light (300) at 2.1rem. Section headings are Medium (500) at 1.1rem. Body text is Regular at 15px. Labels are Medium at 0.875rem, in sentence case rather than spaced capitals.
- **Mono for details**, as the brand uses NB Grotesk Mono for metadata. Geist Mono is reserved for identifiers, page counters, link IDs, copied links and the watermark.
- **Tabular figures** wherever numbers line up.
- **No serif.** PP Hatton cannot be licensed for an open-source project, and a substitute serif in a product UI would read as decoration.

### 3.4 Colour

Each token is written once with CSS `light-dark()`, resolved by `color-scheme` on the root element, so there are no duplicated dark-mode blocks.

**Dark is the default** on both surfaces. A round three-state toggle in every header cycles light, dark and system (system follows the operating system). It shows Phosphor's moon, sun and half circle. Its label shows the current state, and the choice is saved per browser. A small `theme-init.js` in `<head>` applies the saved choice before first paint; it is a separate file because the CSP forbids inline scripts. Contrast figures below are the lowest measured against any surface the token is used on.

**Admin palette** (Negroni).

| Token | Light | Dark | From | Lowest contrast (light / dark) |
|---|---|---|---|---|
| `--bg` | `#fdfbf9` | `#1A0B00` | Vermouth 500 in dark | |
| `--surface` | `#ffffff` | `#241307` | | |
| `--tint` | `#f7efe8` | `#2e1b0d` | Vermouth 100, muted | |
| `--border` | `#ece3db` | `#3a2516` | | |
| `--text` | `#1A0B00` | `#f6e8dc` | Vermouth 500 / 100 | 16.9 / 13.7 |
| `--text-2` | `#6a5141` | `#d2b9a5` | Vermouth, desaturated | 6.4 / 8.8 |
| `--text-3` | `#7a6253` | `#ad927e` | | 5.0 / 5.6 |
| `--link` | `#B24400` | `#FF8133` | Campari 500 / 200 | 5.0 / 6.6 |
| `--action` with `--on-action` | `#FF6200` with `#1A0B00` | same | Campari 300 with Vermouth 500 | 6.4 |
| `--ok` | `#127D47` | `#1ABC6B` | PCB 400 / 200 | 4.6 / 6.6 |
| `--warn` | `#8a5a00` | `#FFAB1A` | Citrus, deepened / Citrus 300 | 5.2 / 8.7 |
| `--danger` | `#a8322b` | `#f08a7e` | | 5.9 / 6.8 |

- **Primary buttons** use the brand's own call-to-action colour, Campari 300, with dark Vermouth text rather than white. White on Campari 300 measures 3.0:1 and fails AA.
- **Chrome.** A 4px Campari 300 bar over a 2px Citrus 300 bar runs across the top of every admin page, adapted from the header bars of the Negroni document template. A short PCB 300 rule sits in the footer. Most of the brand's colour is in this chrome, so the rest of the page can stay quiet.
- **Reading intensity**, used in the admin's reading heatmap, runs Citrus 100, Citrus 300, Campari 300, Campari 500 in light mode, and from dim amber up to Campari 200 in dark mode.
- **One accent.** Campari is the only action colour. Citrus appears only in the chrome and the heatmap. PCB appears only for "all good" states.
- The taste guide warns against warm near-black text and cream backgrounds as a default "premium" look. Snug uses them because they are the Negroni brand book's own colours.

**Viewer palette** (neutral stone, so any founder colour sits well on it).

| Token | Light | Dark |
|---|---|---|
| `--bg` | `#fafaf9` | `#1c1917` |
| `--surface` | `#ffffff` | `#262220` |
| `--text` | `#1c1917` | `#f5f5f4` |
| `--text-2` | `#57534e` | `#d6d3d1` |
| `--text-3` | `#6b6661` | `#a8a29e` |
| `--brand`, `--brand-text`, `--on-brand` | From `/brand/brand.css`, chosen by the server to pass 4.5:1 | |

### 3.5 Shape, depth and motion

- **One radius rule.** 8px on everything rectangular (buttons, inputs, panels). 4px on small inline labels. 2px on document pages, so they read as paper.
- **No shadows except under the document page** in the reader.
- **No decorative dots.** Status labels are tinted text ("Active", "Expired", "Revoked").
- **Almost no motion.** Hover changes a colour, and a pressed button moves down by 1px. Everything is turned off under `prefers-reduced-motion`.

### 3.6 Icons

Phosphor icons, in the Regular weight (MIT licence), vendored as SVG files and pinned to `phosphor-icons/core` v2.0.8. They are inlined by the `{% icon %}` template tag and take `currentColor`, so they follow the theme. The GitHub mark in the footer is GitHub's own, used as its logo guidelines allow.

### 3.7 Admin interface

Each page has its own URL, with no hash routing, because every action is a CSRF-protected form post.

**Header.** The chrome bar, then the Snug lockup (section 3.14), the admin's email address, the theme toggle and *Sign out*. The tabs are **Overview**, **Documents**, **Rooms**, **Links**, **People**, **Audit log** and **Settings**. The active tab is underlined in Campari.

- **Overview.** A status line, a stat strip, and recent activity with first opens marked "New". The strip counts documents, active links, people who have visited, and visits in the last 7 days. Recent activity lists the ten latest visits: who, which document (and room), reading time and when. The status line shows any problem from the `snug doctor` checks, such as an old backup or failing email, in an amber notice with a pointer to `snug doctor`; with none, it shows "Everything is working." Housekeeping, the backups, whether email is set up and the country data are checked on each visit. The slower checks (the document worker, DNS and HTTPS) are run by housekeeping every hour and stored, and Snug records whether each email went, writing only when that changes.
- **Documents.**
  - The list shows name, current version, what it is ("12 pages", "Excel workbook, 3 sheets" or "MP4 video, 2:05") and upload date.
  - The page title is set in Geist Light, with the version, what it is and upload date beneath it.
  - **Stat strip.** Viewers, visits, median reading time, and how many read every page. These are large figures separated by hairlines, with no card boxes.
  - **How it was read.** One aligned grid. The top row is a bar chart of average time per page. Below it, each viewer has a row of cells, one per page, shaded by time spent; empty cells were never seen. The grid is at least 18 columns wide, so a short document's cells stay small. Visits, total time and last seen sit to the right. Because the page columns line up, the founder can see where attention went and where each person stopped, in one view. Each cell shows its value as a tooltip, and the totals are in text.
  - **A workbook** has no reading chart. Its page has a *Description* for investors, then its *Sheets*, with a note that it is shared for download only.
  - **A video** has a *Preview* player, and its stat strip and chart are labelled for watching: *Median watching time*, *Watched to the end*, *How it was watched*, and *Average per 10 seconds*, with columns labelled by time.
  - **Links** as plain rows, with *New link* and *Revoke* where they apply.
- **Rooms.** A list, and for each room its documents in folders, its links, and five tools in a two-column grid: add a document, add a folder, the NDA switch, the UK-only switch, and rename.
- **Links.** Every link, newest first: recipient, note, reference, target, status, its settings (verification, downloads, UK only) and expiry. Revoking goes through a confirmation page that shows the recipient.
- **People.** One row per viewer email address, with everything that person has opened across the instance, using the same page strips (a video's cells are its 10-second parts; a workbook has one cell, shaded by the time its card was open). Each document row also shows how many times that person downloaded it, and the person's summary line gives their total when it is above zero. The counts come from the audit log's download events, which outlast reading data, so only documents the person is still shown reading are counted.
- **Audit log.** A dense, filterable table and a CSV export.
- **Settings.**
  - Admins: the list, with inviting as a row at its foot (email address, code and button side by side); remove; reset second factor.
  - Your account: change your password, with the current one and a code from the authenticator; and replace your authenticator, with the password and a code from the old one or a recovery code.
  - Branding: the company's name, as investors see it on their pages and in every email (`SNUG_COMPANY_NAME` is used until one is saved); the accent colour on investors' pages, with a picker, a live preview, and a note when Snug deepens a shade to keep it readable; the logo, uploaded, replaced or removed, with the current one shown on its light tile; and the tab icon, with the one investors see now and why.
  - Email: a sentence on which settings are in force; the choice of Google Workspace or another provider over SMTP, with *Clear email settings* beneath it in the same panel once anything is saved; then numbered steps, each save landing on the next (the client or SMTP server; for Google, the sending mailbox; *Send a test*). Saved passwords and secrets show as dots and are never shown again.
  - NDA: the instance's one agreement, versioned.
  - Privacy: an optional link to the founder's own privacy policy, and how long reading data is kept.
  - Each section with a guide links to it in its heading, with a book icon, in a new tab so a half-filled form is not lost: the admin accounts guide for Admins and Your account, the guide for the chosen email provider, and the NDA guide.
- **New link.** The form is on the left. On the right is what happens next. Creating the link opens a page with the link to copy, shown once.

**Phones.** One breakpoint at 860px for layout and one at 640px for spacing. On phones, the stat strip becomes two by two. Each viewer's row in the reading grid stacks: email address, then the page strip, then "3 visits, 17 min 26 s". Link rows drop their secondary columns.

**Login.** Email and password, then a six-digit code. A *Use a recovery code* link leads to a separate form, and *Forgotten your password?* to the reset flow in section 1. First-time enrolment shows the QR code, then the recovery codes once.

### 3.8 Viewer

The company's logo at the top left with its name beside it, or either alone, and nothing competing with the document, designed phone-first.

1. **Landing.** "**Fernlea Robotics** has shared **Seed deck** with you", then an email field and *Send code*. On wide screens, an **About this link** panel sits beside it:
   - who sent it, which document, the access given and the expiry;
   - the cover message, if the founder wrote one;
   - a statement of what the company will see: when the viewer opens the document and how long they spend on each page, and that every page shows their email address; for a video, how long they watch each part, with their email address over the video; for a workbook, when they open it, how long its page is open, and when they download it, and that their email address is written into their copy's properties; for a room, when they open each document and how long they spend on it, and that every page and video shows their email address.
2. **Code.** One six-digit field (`autocomplete="one-time-code"`). Errors appear below it and say what happens next: "That code is not right. You have 4 tries left."
3. **NDA**, when required, headed "Before you open" and the document's name. The NDA text in a readable panel, a name field, an *I accept this agreement* checkbox, and a statement of what is recorded (name, email address, time, IP address, browser and a fingerprint of the text). The page has a note that this is a record of acceptance, not a signature.
4. **Room index.** Folders as plain section headings (one level only), each document with what it is ("12 pages", "Excel workbook, 3 sheets" or "MP4 video, 2:05") and last update.
5. **Reader.** One page image at a time, with *Previous*, "5 of 18", *Next* and a page picker. Arrow keys work and pinch-zoom is never disabled. The watermark footer reads "Shared with *email*", the date, and "Link 7Q2K" in three columns, with no separators. *Download* gives the original PDF, stamped for the reader, unless the founder has turned downloads off for this link. A workbook shows a card instead, with its description, its sheets and *Download the workbook*; a video plays in a player with the watermark drawn over it and Snug's own full-screen button. Below the page or the video is the *About this link* panel, with the cover message, so it never moves what the investor is reading.
6. **Unavailable.** One page for unknown, expired and revoked links alike.

**Founder branding under a strict CSP.**
- The accent colour shows as a band across the top of every investor page, as the admin has in orange, and on their buttons, links and the reader's Download button. It reaches viewer pages through a generated `/brand/brand.css`, linked with a fingerprint of the colour so a change is fetched at once (`snug.core.brand`). The server works out three shades: the button fill (`--brand`), text and links (`--brand-text`, which viewer links use) and the button label (`--on-brand`, white or near-black). Each is nudged towards black or white until it reaches 4.5:1 against what it sits on, in light and dark mode. Emails have the company's name but not the colour, since colouring them would need inline styles.
- The logo is accepted as PNG or JPEG only, up to 2 MB. SVG is not accepted, because an uploaded SVG can contain script. The render worker decodes it in its confinement, as it does a PDF, and draws a fresh PNG of at most 800 by 240 pixels, so nothing but the pixels survives (`snug.core.logo`). It is stored as `branding/logo-<fingerprint>.png`, and served at `/brand/<that name>` only while it is the current logo, with a long cache because a new logo gets a new name.
- Investors see it at the top of every page, with the company's name beside it (so the image has empty alt text) unless the founder unticks Show the name beside the logo, for a logo that spells it out. In dark mode the logo sits on a light rounded tile, since most logos are drawn for a light page.
- The tab icon is the founder's own square icon if they upload one, redrawn by the worker on a clear 180-pixel square; else the logo, if it is within 5:4 of square; else a rounded tile with the company's initial on the accent colour, generated as `/brand/initial.svg`. A browser draws a tab icon in a small square, where a wide logo would be an unreadable sliver.

### 3.9 Email

- Plain text plus a simple HTML version, with the company's name. The test emails from Settings and `snug doctor` are plain text only.
- No remote images and no tracking pixels.
- The code email's subject is "Your code for *Fernlea Robotics*: 482913", so the code can be read from the notification. The body has the document's name and a line to ignore the email if it was not requested.
- First-open emails to admins give who (with the link's note), what (and the room), when and where (the country's name, or *Not known*), with a link to that person on the People page, whose anchor is a hash of the address so no address is in a URL. They are sent the first time anyone opens a document through each link, which a single conditional update on `Link.first_opened_at` claims, so two pages loading together send one email; opening a room's list is not an open. Every working admin gets them unless they untick the box under Your account in Settings. They are sent in the background, so the reader never waits.

### 3.10 Front-end architecture

- **Templates.** Django templates with `{% include %}` partials. No front-end framework and no build step.
- **Stylesheets.** One stylesheet, `snug.css`, holds the tokens and components for the admin and the viewer, and a generated `/brand/brand.css` sets the founder's colour on viewer pages. Static files are fingerprinted by `ManifestStaticFilesStorage` and served by whitenoise. Fonts and icons are served from the instance.
- **Charts.** Server-rendered inline SVG. Geometry is set by attributes, which the CSP allows, and colour comes from CSS classes. The reading grid's chart and per-viewer strips share one coordinate system of at least 18 slots, which keeps them aligned at any width.
- **JavaScript**, as separate files: `theme-init.js` (blocking, tiny, in `<head>`), then deferred `theme.js` (the toggle), `admin.js` (copy buttons, the tabs on a narrow screen, and upload progress: it sends an upload form itself, and the server answers that request with the address to go to, since a followed redirect would lose its `#section`), `viewer.js` (the reader's keys and page picker, and reading-time heartbeats sent only while the tab is visible, from the reader and a workbook's card), `video.js` (the video player's parts, heartbeats and full screen), `reveal.js` (showing a password), `brand.js` (the colour picker and its preview) and `nda-first.js` (the box for writing the first NDA). Every page works without JavaScript.
- **CSP.** `default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; font-src 'self'; connect-src 'self'; manifest-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'`. The default is `'none'`, so anything not listed is refused.

### 3.11 Empty, loading and error states

- **Empty.** An empty list has a line on what will appear there and how to start. For example: "No documents yet" and "Upload a PDF, an Excel workbook or an MP4 video to get started", above the upload button. Or: "Nobody has opened this document yet. Reading time appears here once someone does."
- **Loading.** The reader reserves each page's exact size from stored dimensions, so nothing moves while it loads. No spinners: the next page is prefetched. An upload shows its progress as a bar on the form, then "Checking the file" while the server reads it, with the button disabled throughout. A large PDF takes a while, and a page that seems to do nothing gets clicked again or abandoned. Without JavaScript the form posts as before.
- **Errors.** Shown inline, beneath the field they concern, saying what to do next. Whole-page errors (an unavailable link, a failed upload) use the same layout as the rest of the page.
- **Passwords.** Every password box has an eye at its right that shows or hides what is typed, for phones where autocorrect makes typing hard. The button appears only once its script has run, and the box turns off autocorrect, capitalisation and spellcheck. A form never submits with the password showing. A saved password or secret, which Snug never sends back, shows as twelve dots in its empty box, so the box does not look unset.
- **Saved.** Every admin save is confirmed. The page returns to the section the save was made in, with a green notice there saying what changed: "Saved. Links to this room no longer ask for the NDA." Shown once; a reload does not repeat it, except in Settings, whose notices come from the address (`?saved=`). A failed save shows its error in red in the same place.

### 3.12 Testing the front end

- **Template tests:**
  - every page sends the CSP header;
  - no template has an inline script, a `style=""` attribute or an `on*=` handler;
  - every posting form has a CSRF token;
  - no template contains an em dash, an en dash or a middle dot.
- **A contrast test** is still to write (section 4). It will parse the token stylesheet, resolve both sides of each `light-dark()` pair, and fail any listed text pairing below 4.5:1, for both the admin and the viewer palettes. For now the token table's ratios were measured by hand, and only the founder's accent shades are tested (`tests/test_brand_colour.py`).
- **Real clicks.** Screenshots miss dead click paths. Playwright (Apache-2.0) runs the investor's journey end to end in CI, along with the admin controls that only a click can prove: the theme toggle, the password eye, the box for the first NDA, on a room and on the link form, upload progress, the email steps and clearing them, the shared columns of every admin list, a workbook uploaded, shared and downloaded, a video uploaded and played with its mark over it, and every admin page at phone width.

### 3.13 Settled questions

1. **Link tokens are stored hashed.** The founder sees the full link once, at creation, and can afterwards send a new link, which stops the old one working.
2. **Page images and accessibility.** The gap is accepted for version 1 and stated in the reader, which has a note that pages are images and, when downloads are allowed (the default), a pointer for screen-reader users to the PDF.
3. **No thumbnails in the reader** in version 1.
4. **Playwright runs in CI**, in a real browser.
5. **A privacy notice for viewers.** Under UK GDPR the founder is the controller of investors' personal data: email address, IP address and reading behaviour. The reader shows a short statement of what is recorded, with an optional link to the founder's own privacy policy, set in Settings.
6. **The logo and forks.** The logo files are MIT with the code, and `TRADEMARKS.md` asks that forks distributed as their own product use a different name and icon.
7. **How long reading data is kept.** Visits and time per page are deleted automatically a set number of months after their link is revoked or expires: 12 by default, set in Settings from 1 to 120, since some sectors, such as healthcare, need seven years. While a link works, its reading data is kept. The link and the audit log stay.

### 3.14 Logo

The Snug icon is a **half-glazed snug door**, chosen from eight concepts. A snug is the small private room in a British pub, which suits a private room for documents and a studio named after a cocktail.

- **Construction.** It is drawn on a 200 by 200 grid in the manner of the Negroni glyph:
  - a square-topped door standing on a threshold, in round-ended 13-unit strokes;
  - a glass pane with a lowercase s cut out of it, as pub snug doors had their name etched in the glass. The cut-out is an SVG mask, so the glyph works on any background;
  - a knob, which keeps it reading as a door at small sizes;
  - the four-pointed Negroni sparkle at the top left.
- **Colourways.** Campari tile with a cream (Vermouth 100) glyph is the primary. Vermouth 500 with a Campari glyph is for dark backgrounds, and Citrus 300 with a Vermouth glyph for bright ones.
- **The lockup.** A square tile, then "Snug" in Geist SemiBold, as the Negroni site sets its own tile beside its wordmark. In the admin header the tile is 40px. The tile is square, an exception to the radius rule, to match Negroni's.
- **Sizes.** The full icon holds down to 32px. The favicon is a simplified drawing for 16 and 32px: no sparkle and no s, heavier strokes, and the door enlarged to fill the tile.
- **Where it appears.** The admin interface, the admin's browser tab, the README and the GitHub social preview. Never on viewer pages, which use the founder's logo and tab icon.
- **Inline use.** The glyph takes `currentColor`. Each inline copy on a page needs its own mask id, so the `{% logo_tile %}` tag takes a suffix.
- **Files** are in `docs/design/logo/`:
  - `snug-glyph.svg`, the glyph alone;
  - `snug-icon-campari.svg`, `snug-icon-vermouth.svg` and `snug-icon-citrus.svg`;
  - `favicon.svg`;
  - `index.html` shows them as browsers load them, and `concepts.html`, built by `build_concepts.py`, keeps all eight concepts.

  There are no PNG sizes yet; the favicon is SVG.

### 3.15 Mockups

- [docs/design/mockups.html](docs/design/mockups.html) shows four screens (admin document, new link, viewer verification, reader), in light and dark and at phone width.
- It is built by [docs/design/build_mockups.py](docs/design/build_mockups.py) from `mockups.template.html`. The sample reading data is in that script, so every figure on the page agrees.
- [docs/design/snug.css](docs/design/snug.css) holds the tokens and components.
- Fonts and icons are in `docs/design/fonts/` and `docs/design/icons/`, each with its licence.
- The mockups are design files only, and Snug does not serve them.

## 4. Order of work

As the brief, with step 0 added: repository guard rails, then branch protection. Each step is made of small pull requests that leave CI green.

Steps 0 to 8 are done, apart from what waits for the first version tag.

**Step 7: deployment.**
- The container image, for amd64 and arm64, published to `ghcr.io/negroni-venture-studios/snug`: `:edge` on every merge to `main`, and `:x.y.z` and `:latest` on a version tag.
  - *Done:* the Dockerfile (Python 3.14 slim, pinned by digest; one unprivileged `snug` user; Landlock confirmed active in the container) and the `Image` workflow.
  - *Done:* the package is public.
  - **Documentation comes before publishing.** No version tag (`v0.1.0`, which publishes `:latest`) until the README's quick start and `docs/deploy-digitalocean.md` describe that release and have been followed on a fresh droplet. Until then the README says that `:edge` is a test build. Settings links each section to its guide on `main` (`DOCS_URL`); once releases are tagged, those links go to the running version's tag instead, so a founder on an older release reads the guide for it.
  - *Done:* `image smoke test` is a required check on `main`.
- *Done:* Docker Compose with Snug behind Caddy (`deploy/compose.yaml`, [docs/deployment.md](docs/deployment.md)). It uses `:edge` until the first version tag, then `:latest`.
- *Done:* Litestream, and an rclone sidecar that copies Litestream's replica, the uploaded originals and the branding images (`originals/` and `branding/`) to a bucket, encrypted by an rclone crypt remote whose password and salt are generated during setup, which the founder must store ([docs/backups.md](docs/backups.md)). `snug setup` generates them and asks the founder to confirm they are saved.
- *Done:* cloud-init for a fresh Ubuntu 24.04 LTS droplet (`deploy/cloud-init.yaml`): Docker from Docker's signed repository, automatic security updates, a firewall for SSH, HTTP and HTTPS, a 1 GB swap file, and the deploy files with the `snug` command. It holds no secrets. The founder then runs `sudo snug setup` over SSH, which asks for the domain, the company's name and the bucket on the server, so no secret is stored with DigitalOcean (decided 6 November 2026). With a saved `rclone.conf` already in place, setup restores instead. CI validates the file with `cloud-init schema` and the scripts with shellcheck; it runs for real on the step 9 droplet.
- cloud-init, `docs/deployment.md` and `docs/backups.md` fetch the deploy files from `main`. At the first version tag they must fetch from the tag instead, and the Compose file must use `:latest` or the version.
- *Done:* `snug doctor`, checking DNS, HTTPS and the certificate's expiry (a problem with under 14 days left, since Caddy renews at 30), email (send a code, type it back), the latest backup (a problem after 15 minutes), the render worker's confinement level (Landlock), and housekeeping (a problem when `snug housekeeping` has not completed in the last three hours; it records each success in the `SystemCheck` table).
- *Done:* the admin Overview's status line shows any problem `snug doctor` would report, including failing housekeeping, so a founder sees it without running a command.
- *Done:* `snug restore` ([docs/backups.md](docs/backups.md)), run on the server through the `snug` command in `/usr/local/bin`: it fetches the bucket's backups, rebuilds the database with Litestream (the latest, or `--at` a moment in the last 7 days), and moves any data it replaces aside rather than deleting it. Each instance has a random identity, kept in the bucket from its first backup; the backup sidecar refuses to copy into a bucket holding another instance's backups, so a new server started before its restore cannot overwrite them. The image smoke test wipes a server, checks the refusal, restores it, and restores onto a server that never started.

**Step 8.**
- *Done:* DB-IP's country data, fetched monthly by housekeeping (`snug/core/geoip.py`).
- *Done:* UK-only mode, per link or per room (`authorise()` check 4), with DB-IP attribution.
- *Done:* first-open email notifications to admins: every admin by default, each with a switch under Your account to turn their own off (decided 6 November 2026).

**Email in Settings.**
- *Done:* email set up in Settings, through Google Workspace's Gmail API or another provider over SMTP (pull requests #88 to #98). On 9 November 2026 the Google setup was tried end to end from a first Google Cloud sign-in, and a test email went out through the Gmail API. Its findings went into the interface (numbered steps, dots for saved secrets, the second code announced, *Clear email settings*), into [docs/email-google.md](docs/email-google.md), and into the fix for the macOS worker crash (`snug.core.outbound`).
- Sending through a real SMTP provider, such as Mailgun or Postmark, is built and tested in code but not yet tried with an account.

**File types.**
- *Done:* Excel workbooks, shared for download only and stamped in their properties, with a description and the sheets on their admin page (pull requests #102 to #106), and MP4 videos, played in the browser with a watermark over them (#109 and #110). The interface text was made plainer (#107 and #111), and every admin list keeps its columns in line (#91 and #108).
- Still to come: previews of workbooks, other Office files, and WebM or MOV video (section 5).

**Next.** The DigitalOcean rehearsal at `snug.negroniventurestudios.com` (step 9), with Google sending set up there; its client already lists that server's redirect address.

**Step 9.** README, `docs/using-snug.md` (how to use the admin interface and what investors see, with annotated screenshots generated by the Playwright tests, so rebuilding the guide refreshes them; *done:* the guide, and `tests/e2e/test_screenshots.py`, which takes its screenshots, framed in a browser window, from the demo data room in `tests/demo.py`), `docs/deploy-digitalocean.md`, `docs/security.md` (including what watermarks, the NDA record and UK-only mode do not protect against, and the limits of the video mark and workbook stamp in the Videos and Spreadsheets rows), then a full deployment to a test droplet from the guide alone, at `snug.negroniventurestudios.com` with an A and an AAAA record (decided 9 November 2026), and a restore onto a second fresh droplet. sslip.io was ruled out. It is not on the public suffix list, so its Let's Encrypt certificates share one weekly limit with every other user, and one sslip.io name holds one address, so IPv4 and IPv6 visitors cannot both be tested. A rehearsal on the same name comes first, before the guides are written. On the first droplet, check that the audit log records a visitor's own address over both IPv4 and IPv6. Both droplets are destroyed afterwards.

**Contrast test.** Before version 1, write the test that section 3 describes, which checks every listed text pairing in the token stylesheet at 4.5:1 or better in both themes.

**After version 1.** Once version 1 is out, each of these becomes a public GitHub issue labelled `enhancement`, along with anything else specified and still not built:
- A second logo for dark mode, in place of the light tile.
- *Extend* on a link, beside *Revoke*.
- Filters on the Links page.
- The documents list's number of links and when each document was last viewed.
- A weekly digest of viewing activity by email, beside the first-open notifications (one of the brief's open questions; not in version 1).
- Settings panels for session lifetimes and the system (version, backup status, the GeoIP database's date with its attribution, and licences). Much of this overlaps `snug doctor` and the Overview's status line. Email has its panel already.

**Held Dependabot updates.** These stay open. Closing a Dependabot pull request makes Dependabot skip that version for good. Merge each once its release is two weeks old.
- #2: the build backend, `uv_build>=0.13.5,<0.14`. A range lets a build take the newest 0.13 release, 0.13.7 today, so it waits for that one and merges with #32 (17 November).
- #32: uv 0.13.7 in the image (eligible 17 November).
- Dependabot offers Python patch releases only, so it will not propose Python 3.15 for the image. Moving to 3.15 is a deliberate change: add 3.15 to CI's test matrix, then take the newest 3.15 patch release, which must include the 25 October security fixes in 3.14.10.

## 5. After version 1 (*proposed*)

**More file types, spreadsheets first.** *Done:* Excel workbooks, download only, stamped in their properties (pull requests #102 to #106, the Spreadsheets row above), and MP4 videos (#109 and #110, the Videos row). Previews of workbooks (a PDF export uploaded beside a workbook, or LibreOffice), other Office files, and WebM or MOV video remain.

- **Spreadsheets are for downloading.** Downloads are on by default for every file type (section 1), and a spreadsheet's download is the working file, changed only in its document properties. A page-image view is only a preview, for deciding whether to download. A link with downloads turned off cannot usefully share a spreadsheet, and the link form's hint says so.
- **Traceable downloads.** Snug cannot burn a visible watermark into a working file. Instead each download is stamped for its reader:
  - the document properties hold the reader's email address, the date and the link (built as the comments and three custom properties);
  - a first sheet, or a header and footer on every sheet, shows who it was shared with (not built: on 9 November 2026 the stamp was kept to the properties, so every sheet stays exactly as uploaded);
  - the audit log records the download, with a hash of the exact file sent.

  `docs/security.md` must say what this cannot stop.
- **Stamping runs in the confined worker.** Opening a hostile XLSX is as risky as opening a hostile PDF. *Done:* `snug.rendering.workbook` writes the stamp with the standard library alone, streaming every other part through unchanged, with no openpyxl and no LibreOffice.
- **Previews.** Converting to PDF for the preview would also run in the worker. LibreOffice in headless mode (MPL-2.0, compatible with MIT) converts XLSX, DOCX and PPTX, but it is large (several hundred MB in the image) and a big parser, so it must be sandboxed like PDFium. Without it, the admin can upload a PDF export alongside the spreadsheet as its preview.
- **Analytics** for a spreadsheet are downloads, plus preview pages read, if there is a preview.
