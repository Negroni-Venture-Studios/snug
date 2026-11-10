# Changelog

All notable changes to Snug are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and Snug uses [semantic versioning](https://semver.org/).

## [Unreleased]

### Security

External security audits found all three.

- The PDF worker refuses to read a file unless it has confined itself: Landlock ABI 4 (Linux 6.7) or later, and a new seccomp filter that refuses to create any socket. It used to carry on unconfined if Landlock failed, and Landlock alone let it send UDP. `SNUG_UNSAFE_UNCONFINED_RENDERING` allows unconfined reading for development only.
- Signing in goes back to the password step if the password or authenticator changes between the password and the code. Someone who knew the old password could otherwise finish signing in, within five minutes, with a code from the app or a recovery code.
- Snug refuses a POST over 64 KB, enough for a form's fields, unless a signed-in admin sent it, and refuses it before reading the body. A visitor who was not signed in could send the upload forms a body as large as the video limit, 1 GB by default, and Snug wrote it to disk before turning them away.

### Fixed

- In light mode, the pale green behind status labels and notices is a little paler, so their green text reaches 4.5:1 on the page background, up from 4.46:1. A new test checks every text colour in the stylesheet, in both themes, against everything it is shown on.
- `snug setup` can be run again on a server already running Snug, to change the domain or the company's name. It used to find the backups in the bucket and start restoring them over the live data, then stop at the restore's confirmation prompt. It also creates the data volume through Snug's own container, so that on a new server Snug's user owns it rather than root.
- The PDF reader and the video page show the *About this link* panel below the page or the video, with the link's message and what the founder will see. A message used to show only on the pages before the document opened.
- The SMTP password and the Google client secret in Settings have the eye that shows what is typed, like every other password box.
- The guides and diagrams match the code again. Changing the secret key signs admins out but leaves investors signed in, `snug doctor` reports a failed backup copy at once, Google email counts only once a mailbox is connected, and the document worker parses a file only once it is confined, where the guides said it read nothing before.
- Four messages are plainer: the upload refused on a server that cannot confine its document worker, the link form's note on downloading a video, the line under a workbook's sheets, and the investor's code email, which no longer ends on a reassurance.
- `SNUG_SMTP_PORT` in `snug.env` is held to the same ports as the email settings in Settings (25, 465, 587, 2465, 2525 or 2587), except on `localhost`. Snug refuses to start with any other, and says which ports it takes.
- The Audit log's filter has two more kinds, *Housekeeping* and *Exports*, for the reading data housekeeping deletes and for exports of the log itself.
- Resetting an admin's authenticator from the command line is recorded as `admin.second_factor_reset`, the same as from Settings, where it was `admin.2fa_reset`.
- With no file chosen, the upload form asks for a PDF, an Excel workbook or an MP4 video, where it left videos out.
- On a UK-only link that asks for the NDA, someone abroad who opened the NDA page got a server error. They now get the same refusal as on every other page of the link, and it is recorded in the audit log.
- While an upload is checked, the form shows *Checking the file*, where it showed *Checking the PDF*.
- Other wording that assumed every document is a PDF now covers workbooks and videos too. `snug doctor` and the Overview call the confinement check *Document worker*, People has a note that a video's strip has a cell for each 10 seconds, and the Overview counts *People who have visited*.
- On the Links page, a revoked link's status and details line up with the active links above and below it. A document's versions and a room's documents share their columns the same way.
- Hints and notices now say only what happens or what to do, and the first-open email says *Not known* for an unplaced country.
- A workbook's page in the admin no longer fails with a server error once an investor has opened it. It tried to draw a page-by-page reading chart for a document with no pages.
- Every viewer event in the audit log records the visitor's country, whether or not the link is UK-only, and the audit log shows it beside the address. The country column had been left empty, though the brief asked for it.
- The Google Workspace guide now starts from a first visit to Google Cloud and covers every screen, SPF, DKIM and DMARC for each sending domain, user alias domains, awkward DNS hosts, and being signed in to more than one Google account.
- A saved Google client secret or SMTP password shows as dots, so the field no longer looks empty. The value itself is still never sent to the browser.
- Before you leave for Google, the mailbox step has a note that one more authenticator code is needed on your return, and on your return a green notice shows the approved mailbox.
- On a Mac, connecting Google killed Snug's server process, and the browser showed an empty response when Google sent you back. Snug's calls to Google and DB-IP now take proxy settings from the environment only, rather than asking macOS, whose frameworks refuse to run in a process gunicorn has forked. A server on Linux was not affected.
- Email in Settings is set up in numbered steps, all on the page from the start: your Google client or SMTP server, then for Google the sending mailbox, then a test. Each save lands on the step to do next, with its notice there. Saving the Google client used to leave Connect Google in a new panel below the page's fold. If Google refuses a connection, Snug now returns to the mailbox step with the reason, and Google's one-time code no longer stays in the address bar.
- In Settings, the admins' statuses and dates line up in columns. Your own row has no buttons, and it used to sit out of line with the others.
- On a phone, the Overview's recent activity no longer pushes the page sideways: each visit shows who, then what. A browser test now checks every admin page at phone width.
- Settings, the new-link form and the room page kept the shared admin script out when adding their own, so their tabs did not scroll to the current one on a narrow screen.
- The README and guides describe Snug as it is: downloads are on by default, UK-only mode is still to come, and local emails can be read with one command. The design record lists the specified features not yet built. The container image now includes `THIRD_PARTY_LICENCES.md`, and pypdf is listed in it.
- Downloading a PDF that opens without a password but is encrypted against copying (common for published PDFs) failed with "could not be prepared for download". It is now stamped from an unencrypted copy. The confined worker also preloads every text codec it may need, since a PDF with UTF-16 metadata failed the same way.
- Links from a Snug on your own computer now work: `SNUG_DOMAIN` can be `localhost` with a port, like `localhost:8000`, and every link and email includes it. The README's trial command uses it. A real domain still cannot include a port.
- The admin and sign-in footers link to Negroni Venture Studios, and to the source on GitHub with GitHub's mark beside the repository name.
- A tab icon for investors' pages, in Settings under Branding: upload a square one, or Snug uses your logo if it is roughly square, and otherwise your initial on your accent colour. A wide logo made an unreadable icon, and with no logo investors saw Snug's own.
- Investors' pages show the company's name beside the logo, rather than the logo alone. Untick Show the name beside the logo, in Branding, for a logo that already spells it out.
- Inviting an admin is a row at the foot of the admins list, the full width of the list, rather than a separate panel.
- The reader's page picker leaves room for its arrow, which sat on top of the page number, and is the same height as the buttons beside it.
- The company's name is set in Settings under Branding, beside the colour. It was only in the server's settings, as `SNUG_COMPANY_NAME`, which is now used only until a name is saved.
- The accent colour shows as a band across the top of every investor page, and on the reader's Download button. Once a document was open, it had been on almost nothing.
- When signing in, a wrong code from the authenticator app now says that codes change every 30 seconds, and the password page shows a message when more than five minutes have passed since the password.
- Settings lists the admins first, then inviting one, then your account. Its buttons are as wide as their labels, Replace authenticator is orange like the others, and a code box beside other fields is the same height as they are.
- The theme toggle's sun, moon and half circle are Phosphor icons, like every other icon in Snug.
- A refused save in Settings opens where it was made, with its red error beside the field and in view, instead of at the top of the page. Inviting, removing or resetting an admin returns to its section too.
- Every save in the admin interface now shows a green notice in the section where it was made, and the page stays there instead of jumping to the top. A room's page shows beside its name when it requires the NDA, and its four tools sit two by two.
- Link expiry dates are shown as the last day the link works. The links list and the link email showed the day after.
- The README's instructions for trying Snug locally now keep the secret key in a file, and say how to update the container and set a forgotten password.
- The server no longer stalls, and then answers "Internal Server Error", when a browser holds connections open without using them. `snug serve` now runs threaded workers (`--threads`, 4 by default).
- An unexpected error is now logged with its traceback. Link and setup tokens in any log line are replaced with `[redacted]`.

### Added

- [Using Snug](docs/using-snug.md), a guide to every page of the admin and what investors see, with screenshots. The deployment, backups, email, admin accounts and NDA guides have diagrams and screenshots too.
- Screenshots in the README, in light and dark, taken from a demo company's data room in a real browser by `tests/e2e/test_screenshots.py`, so they can be retaken whenever a page changes.
- [How Snug works](docs/architecture.md), a guide to the code for developers, with diagrams in light and dark.
- Snug counts how long an investor has a workbook's page open, as it does for a page of a PDF. On People, a workbook shows one cell shaded by that time, where it used to say *Spreadsheet*. Investors are told this on the workbook's page.
- MP4 videos can be shared as documents. Investors watch them in the browser with their email address, the date and the link drawn over the player, and Snug counts time watched in 10-second parts, shown on the document's page and on People. They can download it when the link allows downloads. Uploads are checked in the confined worker, which accepts H.264 video with optional AAC sound and refuses QuickTime and other formats with a plain message. Videos have their own size limit, `SNUG_MAX_VIDEO_MB`, 1 GB by default.
- A workbook can have a description, written on its page in the admin: plain text, kept across new versions, and shown to investors above its list of sheets.
- Excel workbooks (.xlsx) can be shared as documents, for download only. Investors see the workbook's sheets and a button to download it, and each copy has their email address, the date and the link written into its document properties, with every sheet left exactly as uploaded. Uploads are checked in the confined worker: workbooks with macros, ActiveX or embedded objects, old .xls files and workbooks with passwords are refused with a plain message.
- Email through Amazon SES works from DigitalOcean: Settings accepts SES's ports 2587 (STARTTLS) and 2465 (TLS), beside 25, 465, 587 and 2525.
- *Clear email settings*, under the choice of provider in Settings and shown whenever anything is saved, forgets the Google client and mailbox or the SMTP details, and withdraws Snug's permission at Google. It needs a fresh code, and every admin is told first. Email then falls back to `snug.env`, or is off.
- Settings links each section that has a guide to it, under its heading: admin accounts, email (the Google Workspace or SMTP guide, following the choice of provider) and the NDA. The guides open in a new tab, so a half-filled form is not lost.
- Google Workspace sending can use another of the mailbox's addresses as the From address, such as one on an alias domain.
- Snug can send email through Google Workspace, as a mailbox on your domain, over the Gmail API: Settings, under Email, then [docs/email-google.md](docs/email-google.md) for the one-time Google Cloud setup. It works from DigitalOcean, which blocks Google's mail ports. Snug can only send from the mailbox, never read it, and connecting needs a fresh authenticator code.
- Email is set up in Settings, under Email, and takes effect at once, with a *Send a test* button. Passwords are never shown again. SMTP settings in `snug.env` still work until something is saved there, and `snug setup` no longer asks about email.
- First-open emails: the first time anyone opens a document through a link, every admin is emailed who, what, when and from which country, with a link to that person on the People page. Each admin can turn their own off under Your account in Settings.
- UK-only mode: a link to a document, or a whole room, can open only for visitors in the UK, Jersey, Guernsey and the Isle of Man. Anyone elsewhere, or anyone Snug cannot place, is turned away and sent no code, and the refusal is in the audit log. Countries come from DB-IP's free database, kept on the server; `snug doctor` and the Overview warn if it is missing or out of date.
- Housekeeping downloads DB-IP's free country database once a month and keeps it in the data folder, for UK-only links. Lookups use that copy alone, so no visitor's address leaves the server.
- The admin sign-in page shows the company's name under the heading, once a name is set in Settings or `SNUG_COMPANY_NAME`.
- `deploy/cloud-init.yaml` prepares a fresh Ubuntu 24.04 droplet: Docker, automatic security updates, a firewall and Snug's files. Then `sudo snug setup` asks for the domain, the company's name and the backup bucket on the server, so no secret is stored with the hosting provider, generates the backup's encryption passwords for you to keep, and starts Snug, or restores it when a saved backup configuration is already in place. [docs/deployment.md](docs/deployment.md) walks through it.
- `sudo snug restore` brings Snug back from its backups, on the same server or a new one: the latest, or as it was at a moment in the last 7 days. Any data it replaces is moved aside and kept. A `snug` command on the server runs Snug's other commands in its container. The backup container refuses to copy into a bucket holding another Snug's backups, so a new server started before its restore cannot overwrite them.
- The admin Overview has a status line: any problem `snug doctor` would report, such as a late backup, failing housekeeping, an expiring certificate or an email that could not be sent, or else that everything is working.
- `snug doctor` checks housekeeping, the backups, the document worker's confinement, DNS, HTTPS and the certificate's expiry, and email, by sending a code you type back. It says what to fix, and exits with status 1 if anything is wrong. [docs/deployment.md](docs/deployment.md) lists the checks.
- Continuous, encrypted backups to a storage bucket of your own: Litestream records every change to the database, and an rclone container copies it, the uploaded documents and the branding images to the bucket every 5 minutes, encrypted on the server with a key only you hold. [docs/backups.md](docs/backups.md) explains how to set it up.
- A Docker Compose deployment: Snug behind Caddy, which gets and renews HTTPS certificates, with Snug's settings in `/opt/snug/snug.env`. [docs/deployment.md](docs/deployment.md) explains it. Both containers have read-only filesystems and no privileges they do not need, and Snug trusts a visitor's address only when it comes from Caddy, over IPv4 and IPv6.
- Your logo on investors' pages, uploaded in Settings under Branding: at the top of every page, on a light tile in dark mode, and as the icon in their browser tab. PNG or JPEG, up to 2 MB. Snug redraws it in the same confined process that reads PDFs, so nothing in the file but the picture reaches investors.
- Your accent colour on investors' pages, set in Settings under Branding with a picker and a live preview. Snug deepens or lightens the shade it uses for text and buttons where it needs to, so everything stays readable in light and dark mode, and says when it has.
- Uploads show their progress as a bar, then "Checking the PDF" while Snug reads it, and the button cannot be pressed twice.
- A cover message on a link: an optional note of up to 1,000 characters, shown to the investor in the About this link panel and in the email with the link.
- Replace your own authenticator in Settings, under Your account, for a new phone: your password and a code from the old app, or a recovery code. You go straight to setting up the new one, with new recovery codes, and the link is emailed in case you close the tab.
- Change your own password in Settings, under Your account, with your current password and a code from your authenticator app. You stay signed in on that browser, everywhere else is signed out, and you are emailed.
- With no NDA yet, ticking "Ask for the NDA first" on a new link, or "Ask for the NDA before anything is shown" in a room's NDA section, shows a box to write it there. Saving publishes it as version 1, as Settings would, instead of sending you away to Settings and losing what you typed.
- The People page shows how many times each person downloaded each document, and their total.
- An eye at the right of every password box shows or hides what is typed, for phones and fiddly keyboards. Password boxes also turn off autocorrect and capitalisation.
- "Forgotten your password?" on the sign-in page. It emails a link that works once, for one hour; setting the new password also needs a code from the authenticator app or a recovery code. The reply never says whether an address belongs to an admin. Without email, `snug reset-password EMAIL` does the same on the server, and is written to the audit log; Django's unaudited `changepassword` is disabled.
- The Overview: how many documents, active links, people who have read and visits this week, then the ten latest visits, with a person's first open of a document marked New.
- A container image, `ghcr.io/negroni-venture-studios/snug`, for amd64 and arm64: `:edge` from `main`, and a version tag and `:latest` from each release. It runs as an unprivileged user, and its render worker is confined by Landlock.
- Reading data is deleted automatically 12 months after its link is revoked or expires, a period you can change in Settings (from 1 to 120 months). `snug housekeeping`, which the server runs every hour, does it, and also clears expired codes, sessions, setup links and rate-limit counts.
- An Audit log tab: every admin and viewer event, newest first, filtered by kind or by email address, 100 to a page, with a CSV export of the same selection. Cells a spreadsheet would run as a formula are defused, and every export is itself recorded.
- The NDA: one agreement for the instance, written in Settings and versioned. A room, or a link to a single document, can ask for it before anything is shown. The viewer gives their name and ticks to accept; Snug records the name, email, link, time, IP address, browser and a fingerprint of the exact text, and asks again when the agreement changes. `docs/nda.md` states that this is a record of acceptance, not a signature.
- Rooms, in the admin interface: create and rename a room, add folders (one level), and add, move and remove documents. A document can be in any number of rooms. Every change is recorded in the audit log.
- Sharing a room: one link opens a page listing the room's documents under their folders, each opening in the reader with its own downloads and reading time. Removing a document from the room stops the link reaching it at once.
- A People page: everyone who has opened a document, most recent first, with what each of them read in the same page strips.

- How a document was read, on its page: viewers, visits, median reading time and how many read every page, then one grid with the average time per page above a strip for each viewer, shaded by time on each page, with their visits, total time and when they were last seen.

- Reading time: each page a viewer is shown, and how long it was in view, measured by Snug from heartbeats sent only while the tab is visible. Visits end after 30 minutes away. Each new visit is recorded in the audit log. Nothing is sent anywhere outside the instance.
- An optional link to your own privacy policy, set in Settings and shown to viewers beside the statement of what Snug records about them.

- The reader: once a viewer is in, each page is shown as an image, one at a time, with Previous, Next, the arrow keys and a page picker. Every page is watermarked for that viewer on each request, with their email address and the date across the page and a footer with their email address, the date and the link. Watermarks are stamped in the same confined worker that renders pages.
- Downloads: when a link allows them (the default), the reader offers the original PDF, marked on every page with the reader's faint diagonal watermark (the viewer's email address and the date) and a line with their email address, the date and the link, and the same details in the file's properties. The text stays selectable and searchable. Each download is recorded in the audit log with a fingerprint of the exact file sent.

- A Settings page listing the admins, and inviting new ones. An invite is an emailed link that works once, for 72 hours, where the new admin chooses a password and sets up their authenticator app. Inviting asks for a fresh code from your own authenticator, and every admin is emailed when someone is invited.
- Removing an admin, or cancelling an invite, and resetting another admin's second factor, from Settings. Both ask for a fresh code from your authenticator and email every admin. You cannot remove yourself, and the last admin can never be removed.

- Links, in the admin interface:
  - a link for one person to one document, with an optional note, an expiry date, and choices for verification and downloads. Downloads are on by default, and can be turned off for a link that must be view-only;
  - emailed to the recipient, or shown once to copy and send yourself, with a copy button;
  - a Links page listing every link, and the links for each document on its page;
  - revoking a link, and replacing it with a new one, which stops the old address working at once.

  Link tokens are stored only as hashes, so a copy of the database cannot be used to open them. Every link action is recorded in the audit log.
- The viewer's first pages, at each link's address:
  - a landing page with the document's name and an *About this link* panel;
  - for links that need verification, an emailed six-digit code, with messages for a wrong address, a wrong code and tries left;
  - for links that do not, a single *Open* button, so an email scanner visiting the link starts nothing;
  - one page for unknown, expired and revoked links alike.

  Code requests and checks are rate limited per address, per link and per inbox, and every step is recorded in the audit log. `SNUG_VIEWER_SESSION_DAYS` sets how long a viewer stays verified (7 days by default). The reader arrives in step 4.
- Email through any SMTP provider, configured with `SNUG_COMPANY_NAME`, `SNUG_EMAIL_FROM` and the `SNUG_SMTP_*` settings. `docs/email.md` covers the provider, the SPF, DKIM and DMARC records, and DigitalOcean's blocked SMTP ports. On `localhost`, emails are written to an outbox folder instead.

- A Documents section in the admin interface:
  - a list of documents with their latest version;
  - uploading a PDF as a new document;
  - a document page showing every version and a server-rendered preview of page 1;
  - uploading new versions.

  Oversized uploads are refused from their declared size, before they are read. Uploads, new versions and refusals are recorded in the audit log.

- Documents and versions, and the machinery behind them:
  - PDFs are checked, hashed and stored under generated names, never their uploaded names;
  - each upload becomes the document's next version;
  - PDFs are read and rendered in a separate worker process, with no access to Snug's settings, with limits on memory, CPU time, output size and open files, and a timeout. The worker is handed an open file rather than a path, and on Linux confines itself with Landlock, so it can open no files except fonts and make no network connections;
  - Snug's own processes are non-dumpable, so other processes of the same user cannot read their memory or environment;
  - rendered pages are cached as WebP, and the cache can be deleted at any time;
  - `SNUG_MAX_UPLOAD_MB` sets the largest upload (100 MB by default).
- `THIRD_PARTY_LICENCES.md`, listing the bundled PDFium components, fonts and icons.

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
