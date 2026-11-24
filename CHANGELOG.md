# Changelog

All notable changes to Snug are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and Snug uses [semantic versioning](https://semver.org/).

## [Unreleased]

## [1.4.0] - 2026-11-24

Links for a whole firm: one link for several people or everyone at an email domain, each reading under their own address, and blocking one person on a link. Also, changing the backup bucket or key on a running server, and a new setup link for an admin stuck resetting.

### Added

- A link can now be for several people, or for everyone at a firm's email domain, such as `northfield.com`: enter them under *Who it is for*, separated by commas. Each person verifies their own address and reads under it, with their own watermark, NDA acceptance, and first-open email. A link's page, opened from its recipient on the Links page, lists the people who have verified on it, and *Block* stops one person at once while the link keeps working for the rest. Free email domains such as gmail.com are refused as domains.
- `sudo snug setup` on a running server can now change the backup bucket or its key, keeping the two passwords that encrypt the backups. Before, there was no supported way to correct a wrong bucket or key, and making new settings meant new passwords that could not read the backups already made.

### Changed

- The first-open email is now sent the first time each person opens a link, not only the first time anyone does.
- After a wrong code, the code page no longer names the email address, and says "That code is not right, or it has expired." for every wrong code, without counting the tries left.
- One link now sends at most 30 codes an hour, to everyone it admits.
- Blocking someone on a link now also ends their sessions and code on it, so if they are unblocked they need a new code.
- A new setup link for an admin who is resetting allows only the wrong passwords that remain of the ten.
- Updating: a link made before 1.4.0 for an address that is not a plain one, such as one at an internationalised domain or with a quoted part, opens for nobody after the update. Its page says so: revoke it and create a new one. A code sent just before the update stops working, and the reader asks for another. Going back to 1.3.0 or earlier needs a backup made before the update.

### Fixed

- A request for a code refused by another limit no longer uses up that inbox's allowance of ten codes an hour.
- An admin whose link to set up their authenticator again had expired, or been used up by wrong passwords, could not sign in and could not be invited, since they were already an admin. Another admin can now choose *Send a new link* beside them in Settings. The page for a used link says who can send another.

## [1.3.0] - 2026-11-24

For keeping track of a raise with many links: *Extend* for a link's expiry, filters on the Links page, links and last views on the Documents list, and a weekly email digest.

### Added

- *Extend*, beside *Revoke* on a link, gives it a later last day, or no expiry, and keeps the same link working, so you no longer have to send a new link to give someone more time. It also makes an expired link work again. Each extension is in the audit log.
- The Documents list shows how many active links open each document, directly or through a room, and the day it was last viewed.
- The Links page has filters: by status, and by a search over the email address, note, reference, and the document's or room's title.
- A weekly digest by email: every Monday morning, who read what and for how long, links opened for the first time, downloads, and links that expire in the coming week. Each admin turns it on under *Your account* in Settings; it is off until they do.

## [1.2.0] - 2026-11-23

The security release: every finding of a security audit of 1.1.3 is fixed, `snug update` checks what it downloads, the document worker may make only the system calls it needs, and LibreOffice can no longer see the converter's shared folders.

### Changed

- Snug's web server is now gunicorn 27, which refuses some malformed requests that 26 accepted, such as a `Transfer-Encoding` that does not end in `chunked`. Browsers and Caddy, which sits in front of Snug, never send them.

### Security

- LibreOffice now runs in a view of the converter's container that holds only the file it is converting and a read-only copy of the system. Before, a file that took over LibreOffice could see the converter's shared folders: it could answer for itself while still running, and so be handed the next file Snug sent. The converter also no longer follows a link LibreOffice leaves in place of its output folder, which gVisor allowed.
- `sudo snug update` and a new server's startup script now check every file they download against checksums published with the release, and pin Snug's two images by the digests published with it, so Docker refuses any other image under the release's tags. Before, someone able to replace an image in GitHub's container registry, without access to the repository, would have had it run by every server that updated. The files are also switched in as a set: if an update is interrupted part way through, the next `sudo snug update` finishes it first. The update to 1.2.0 itself runs 1.1.3's `snug update`, which checks nothing and leaves the images pulled by tag, so once a server is on 1.2.0, run `sudo snug update` once more: it checks the files and pins both images.
- The document worker's seccomp filter now allows only the system calls the worker makes, and answers any other as if the kernel had no such call. Before, it refused a fixed list and allowed everything else, so a compromised worker could still reach parts of the kernel it never uses, such as keys, BPF programs, `userfaultfd`, and performance events, which are common routes to a kernel exploit. The LibreOffice converter's filter is unchanged.
- The LibreOffice converter now ends every process a conversion started before it answers, and writes its answers under a random name that nothing in its outbox can block. Before, a process started inside a compromised LibreOffice could leave its process group, outlive the conversion, and read and answer the next file Snug sent, or leave a pipe in the outbox that stopped the converter for good.
- When you restore by pasting backup settings into `sudo snug setup`, it now shows which bucket they are for and when its newest backup was copied, and asks before restoring. Before, settings pasted from the wrong place would have restored another Snug's database, with its admins.
- `sudo snug update` no longer moves a server back to an older release. GitHub's latest release is the one published most recently, not the highest version, so an older release published again would have moved every updating server back. To go back on purpose, set `SNUG_REF`.
- `sudo snug restore` over existing data now moves Office files' previews and part-uploaded files aside with the rest, and a preview is named for its version's stored file rather than its number. Before, a restored version could show another document's pages, stamped as its own, from a preview left over from before the restore. Updating remakes each Office preview once, under its new name, showing the file as a card meanwhile.
- A request refused by a rate limit, or a UK-only link opened from abroad, is now written to the audit log once in each limit's window for an address and an account or link, not once per request. Audit entries are kept for good and backed up, so one client sending refused requests could grow the database and its backups as fast as it could send them.
- An NDA acceptance is now recorded only for the version the investor was shown. If the NDA was changed while their page was open, Snug shows them the new text and records nothing until they accept it. Before, the acceptance was recorded against the new version, which they had not seen.
- After ten wrong addresses on a link, a wrong code now gets the same neutral reply until any code sent in that time has expired, and an expired code then reads exactly as no code. Before, the neutral replies ended on the hour, and a wrong code just after it could tell someone guessing addresses whether their last batch held the right one.
- The document worker can no longer have the kernel signal another of Snug's processes. On Linux 6.7 to 6.11, where Landlock does not yet scope signals, a compromised worker could name another process as the owner of a pipe or socket with `fcntl` or `ioctl`, and the kernel would signal it, stopping Snug until Docker restarted it. The worker's seccomp filter now refuses those commands, and so does the LibreOffice converter's.
- The page for setting up a new authenticator, opened from the link emailed when another admin resets your second factor or when you replace your own, now asks for your password. Before, someone who could read an admin's email while that link was waiting could add their own authenticator, then use the forgotten-password link to change the admin's password as well. Five wrong passwords spend the link.
- Link and setup tokens are now kept out of the logs when a request's path spells `/v/` or `/admin/setup/` with percent-encoded characters, as in `/%76/`. gunicorn logs the whole request line of a request it refuses, so such a path reached the log with its token.

## [1.1.3] - 2026-11-13

Setup says how to create the first admin if that step fails. Also the README's platforms section, and the serial comma throughout.

### Changed

- The README has a section on Windows, macOS, and Linux. Snug runs on a Linux server, and founders and investors use it in a web browser on any of them, or on a phone. The guide to trying Snug on your own computer covers a Mac and Linux; Snug has not been tried on Windows.
- Lists of three or more use the serial comma throughout the interface, emails, and documentation.

### Fixed

- When `sudo snug setup` could not create the first admin, after three refused passwords or codes, it stopped there. It now says to run `sudo snug createadmin`, then finishes with the address to sign in at, as before.

## [1.1.2] - 2026-11-13

`snug version`, and the version in `snug doctor`.

### Added

- `sudo snug version` prints the version of Snug running, as the Overview shows it, and `sudo snug doctor` shows it on its first line. Before, `snug version` printed the version of Django, which Snug runs on.

## [1.1.1] - 2026-11-13

Clearer wording in the *About this link* panel, and a correction to the backups guide.

### Changed

- *About this link* no longer says that the investor's email address is watermarked on what they read, since they can see it, or repeats what an Office file's card says under its download button. Beside a preview's pages it still says that their email address is written into the document properties of a downloaded copy.
- The *About this link* panel tells investors that their IP address, browser, and country are recorded with each visit, as the audit log and the first-open email show them.
- The backups guide no longer says that versioning keeps deleted backups out of reach of someone who takes over the server, since the same keys can usually delete earlier versions too. It suggests object lock, or a copy taken from another computer.

## [1.1.0] - 2026-11-13

Support for Word and PowerPoint files, an optional LibreOffice converter that shows Office files in the browser, and pages that appear sooner.

### Added

- Word documents (.docx) and PowerPoint presentations (.pptx) can be uploaded and shared for download, as Excel workbooks are. Each download has the investor's email address, the date, and the link in its document properties, and the founder can add a description. A presentation's card shows its slide count.
- An optional LibreOffice converter shows Word, PowerPoint, and Excel files in the browser, as watermarked pages with the time spent on each, and a download is still the file itself. Each slide is a page, and each sheet of a workbook is a page sized to the sheet; a small sheet is shown at its own size, on a page as wide as A4. The converter runs in a container of its own under gVisor, with no network, and Docker restarts it after each file. A document's page shows the preview's progress as it is made, and a new version waits for its preview before links show it.
- `snug setup` asks whether to show Office files in the browser, and `sudo snug update --with-office` or `--without-office` adds or removes the converter later. From 1.0.0, run `sudo snug update` first, since 1.0.0's `snug` command does not pass these options on. Settings has an Office files section, between Branding and NDA, with the LibreOffice version, whether an update is waiting, and a switch for showing Office files in the browser; `snug doctor` checks the converter.
- A document can be renamed from its page, as a room can.

### Changed

- Snug refuses a Word, PowerPoint, or Excel file that fetches a template, picture, or object from another computer when it is opened, including through a Word field such as INCLUDEPICTURE. A link to a template on a local disk is allowed, since Office looks for it on the reader's own computer.
- Snug refuses a Word document with a DDE field, or a workbook with a DDE or OLE link, which start another program when the file is opened, and a Word document that contains another, such as a web page, for Word to import when it opens.
- Snug refuses a Word field built from other fields, which it cannot check, unless it is one of the fields a mail merge uses to compare, calculate, or set text.
- An older .doc, .ppt, or .xls file is refused with one message for all three, saying to save it as .docx, .pptx, or .xlsx.
- Snug accepts a presentation or document with a chart. The chart's data is a workbook inside the file, and Snug checks it, and any other Office file inside an upload, as it checks an upload.
- The README and guides state facts plainly, without hedges such as "at the time of writing" or notes on how Snug was tested before its release.
- "What Snug does not do" in the README lists what Snug leaves out, each item starting with "No".
- A room's Add a document panel says the documents listed are not in the room yet, and that adding one shares it with everyone who has a link to the room. No document is chosen until the admin chooses one.
- In the reader and on the video page, *About this link* opens from a button at the top right, beside *Download*, instead of sitting below the page, and *Previous*, *Next*, and the page picker sit above the page.
- A room's folders have a folder icon, and their names are larger and bolder than the documents' titles, for investors and admins alike. On an investor's room page each folder shows how many documents it holds, with its documents indented under it.
- Pages appear sooner. On a one-CPU server the first page of a document took up to 11 seconds to appear, blank meanwhile. Page 1 is now rendered when the file is uploaded or its preview is ready, the next page is fetched only once the current one has arrived, the watermark is drawn about twenty times faster, and the confined worker no longer compiles Python's code each time it starts. Until a page arrives, the reader shows "Loading page" and its number.
- The first pages are stamped with the investor's watermark while they are on the link's landing page, and each stamped page is kept until the end of the day, so a page they return to appears at once. Revoking a link deletes its stamped pages.
- On a link that allows downloads, the privacy statement in *About this link* says the sender sees when the investor downloads. On a link that does not, it no longer mentions a downloaded copy.

### Removed

- The note under each page in the reader that a screen reader can read only the page numbers. [Using Snug](docs/using-snug.md) explains it to founders.

### Fixed

- A long file name in a document's list of versions wraps within its column instead of running over the description beside it.
- `snug setup` asks a yes-or-no question again until the answer is yes or no, instead of taking any other answer as no.
- The preview panel on a document's page, and the Office files hint in Settings, say that investors without a preview see a card describing the file. They used to promise a download button, which appears only on links that allow downloads.
- When Office files are shown in the browser, the *Allow download* hint on a new link no longer says that, without downloads, investors see only what an Office file is.
- When a link reaches its download limit, Snug says "Too many downloads on this link. Try again in an hour." It used to say to wait 15 minutes, but the limit is counted by the hour.
- The Links page and the Overview say that links are created from a document's or a room's page, and a new room link's page says to send a replacement from the room's page.
- When too many codes have been sent to one address, Snug says to try again in an hour, since that limit is counted by the hour. It used to say to wait 15 minutes.
- After a restore onto a new server, Snug makes the Office files' previews again, which are not backed up, and shows the files as cards until each is ready. A page whose PDF has gone says it cannot be shown.

## [1.0.0] - 2026-11-11

The first release.

### Changed

- The design document is now [docs/design.md](docs/design.md), beside the architecture guide, and describes Snug as built. How to release is in [CONTRIBUTING.md](CONTRIBUTING.md#releasing).

### Removed

- The original brief, `docs/BRIEF.md`. What it asked for and Snug does not do yet is in the [issues labelled enhancement](https://github.com/Negroni-Venture-Studios/snug/issues?q=is%3Aissue+is%3Aopen+label%3Aenhancement).

### Security

External security audits found these.

- New servers and `snug update` download the server's files from the newest release, not from the `main` branch, and stop if they cannot find it, so a server runs only what has been released.
- The document worker refuses to read a file if it cannot set its memory limit, and its seccomp filter refuses to trace, signal, re-prioritise, or change the limits of other processes, or read their memory. The watermark's drawing is capped whatever the page's shape, so a PDF page of extreme shape no longer stops its download being stamped.
- Snug believes the `X-Forwarded-Proto` header only from a trusted proxy, so a request over plain HTTP can no longer pass for HTTPS if Snug's port is ever reached without Caddy.
- A refused upload replaces only the main part of the admin page, and nothing in the reply runs.
- The content security policy no longer allows `data:` images.
- `snug setup` clears the screen and the terminal's scrollback once you type `saved`, where the backup settings used to stay visible.
- New servers swap only under memory pressure (`vm.swappiness` 10), so less of Snug's memory, including its secret key, is written to the swap file.
- The document worker refuses to read a file unless it has confined itself: Landlock ABI 4 (Linux 6.7) or later, and a new seccomp filter that refuses to create any socket. It used to carry on unconfined if Landlock failed, and Landlock alone let it send UDP. `SNUG_UNSAFE_UNCONFINED_RENDERING` allows unconfined reading for development only.
- Signing in goes back to the password step if the password or authenticator changes between the password and the code. Someone who knew the old password could otherwise finish signing in, within five minutes, with a code from the app or a recovery code.
- Snug refuses a POST over 64 KB, enough for a form's fields, unless a signed-in admin sent it, and refuses it before reading the body. A visitor who was not signed in could send the upload forms a body as large as the video limit, 1 GB by default, and Snug wrote it to disk before turning them away.

### Fixed

- A backup status file that is not readable text no longer breaks Settings and the Overview.
- [What Snug protects against](docs/security.md) no longer overstates four things: admins are alerted only once email is set up, investors' notice beside a document does not mention their IP address, video downloads are not stamped, and backups need a bucket. It now also covers guessed codes and passwords, the confined worker that reads uploads, and HTTPS.
- A new server's startup script includes Docker's signing key, instead of fetching it from Ubuntu's keyserver. When the keyserver failed, the server was left without Docker and said nothing; `snug setup` and the login message now say if Docker is missing.
- Uploading a new document no longer holds up every other change to the database while Snug checks the file.
- If Snug cannot find a free link reference, the link form says so instead of showing a server error. Snug tries 50 references, up from 5.
- Six digits in a script other than ASCII are tried as a recovery code, not as a code from the authenticator app.
- An investor who accepts the NDA twice at once is recorded once. Upgrading removes any duplicate acceptances already recorded, keeping the earliest.
- Two admins publishing the NDA at the same moment no longer get a server error: the later save becomes the next version.
- The README's status says Snug has been set up and restored on a real server, the guides match the code again, and several messages are plainer, among them the revoked-link notice and the forgotten-password email.
- A new server set up with `deploy/cloud-init.yaml` restarts once at the end of its installation if Ubuntu's updates need it, such as a new kernel. Ubuntu used to leave the server showing *System restart required*.
- In light mode, the pale green behind status labels and notices is a little paler, so their green text reaches 4.5:1 on the page background, up from 4.46:1. A new test checks every text colour in the stylesheet, in both themes, against everything it is shown on.
- `snug setup` can be run again on a server already running Snug, to change the domain or the company's name. It used to find the backups in the bucket and start restoring them over the live data, then stop at the restore's confirmation prompt. It also creates the data volume through Snug's own container, so that on a new server Snug's user owns it rather than root.
- The PDF reader and the video page show the *About this link* panel below the page or the video, with the link's message and what the founder will see. A message used to show only on the pages before the document opened.
- The SMTP password and the Google client secret in Settings have the eye that shows what is typed, like every other password box.
- The guides and diagrams match the code again. Changing the secret key signs admins out but leaves investors signed in, `snug doctor` reports a failed backup copy at once, Google email counts only once a mailbox is connected, and the document worker parses a file only once it is confined, where the guides said it read nothing before.
- Four messages are plainer: the upload refused on a server that cannot confine its document worker, the link form's note on downloading a video, the line under a workbook's sheets, and the investor's code email, which no longer ends on a reassurance.
- `SNUG_SMTP_PORT` in `snug.env` is held to the same ports as the email settings in Settings (25, 465, 587, 2465, 2525, or 2587), except on `localhost`. Snug refuses to start with any other, and says which ports it takes.
- The Audit log's filter has two more kinds, *Housekeeping* and *Exports*, for the reading data housekeeping deletes and for exports of the log itself.
- Resetting an admin's authenticator from the command line is recorded as `admin.second_factor_reset`, the same as from Settings, where it was `admin.2fa_reset`.
- With no file chosen, the upload form asks for a PDF, an Excel workbook, or an MP4 video, where it left videos out.
- On a UK-only link that asks for the NDA, someone abroad who opened the NDA page got a server error. They now get the same refusal as on every other page of the link, and it is recorded in the audit log.
- While an upload is checked, the form shows *Checking the file*, where it showed *Checking the PDF*.
- Other wording that assumed every document is a PDF now covers workbooks and videos too. `snug doctor` and the Overview call the confinement check *Document worker*, People has a note that a video's strip has a cell for each 10 seconds, and the Overview counts *People who have visited*.
- On the Links page, a revoked link's status and details line up with the active links above and below it. A document's versions and a room's documents share their columns the same way.
- Hints and notices now say only what happens or what to do, and the first-open email says *Not known* for an unplaced country.
- A workbook's page in the admin no longer fails with a server error once an investor has opened it. It tried to draw a page-by-page reading chart for a document with no pages.
- Every viewer event in the audit log records the visitor's country, whether or not the link is UK-only, and the audit log shows it beside the address. The country column had been left empty.
- The Google Workspace guide now starts from a first visit to Google Cloud and covers every screen, the SPF, DKIM, and DMARC records for each sending domain, user alias domains, awkward DNS hosts, and being signed in to more than one Google account.
- A saved Google client secret or SMTP password shows as dots, so the field no longer looks empty. The value itself is still never sent to the browser.
- Before you leave for Google, the mailbox step has a note that one more authenticator code is needed on your return, and on your return a green notice shows the approved mailbox.
- On a Mac, connecting Google killed Snug's server process, and the browser showed an empty response when Google sent you back. Snug's calls to Google and DB-IP now take proxy settings from the environment only, rather than asking macOS, whose frameworks refuse to run in a process gunicorn has forked. A server on Linux was not affected.
- Email in Settings is set up in numbered steps, all on the page from the start: your Google client or SMTP server, then for Google the sending mailbox, then a test. Each save lands on the step to do next, with its notice there. Saving the Google client used to leave Connect Google in a new panel below the page's fold. If Google refuses a connection, Snug now returns to the mailbox step with the reason, and Google's one-time code no longer stays in the address bar.
- In Settings, the admins' statuses and dates line up in columns. Your own row has no buttons, and it used to sit out of line with the others.
- On a phone, the Overview's recent activity no longer pushes the page sideways: each visit shows who, then what. A browser test now checks every admin page at phone width.
- Settings, the new-link form, and the room page kept the shared admin script out when adding their own, so their tabs did not scroll to the current one on a narrow screen.
- The README and guides describe Snug as it is: downloads are on by default, and local emails can be read with one command. The container image now includes `THIRD_PARTY_LICENCES.md`, and pypdf is listed in it.
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
- The theme toggle's sun, moon, and half circle are Phosphor icons, like every other icon in Snug.
- A refused save in Settings opens where it was made, with its red error beside the field and in view, instead of at the top of the page. Inviting, removing, or resetting an admin returns to its section too.
- Every save in the admin interface now shows a green notice in the section where it was made, and the page stays there instead of jumping to the top. A room's page shows beside its name when it requires the NDA, and its four tools sit two by two.
- Link expiry dates are shown as the last day the link works. The links list and the link email showed the day after.
- The README's instructions for trying Snug locally now keep the secret key in a file, and say how to update the container and set a forgotten password.
- The server no longer stalls, and then answers "Internal Server Error", when a browser holds connections open without using them. `snug serve` now runs threaded workers (`--threads`, 4 by default).
- An unexpected error is now logged with its traceback. Link and setup tokens in any log line are replaced with `[redacted]`.

### Added

- The Overview shows which Snug is running, under the status line: a release's version, or the commit of an edge build from `main`, with a link to the guide on updating. Settings links each section to the guides for the running release.
- [What Snug protects against](docs/security.md), including what its watermarks, download stamps, NDA record, and UK-only mode cannot stop.
- Settings shows when the backups last reached the bucket, as *Working* or *Problem*, with the same sentence as `snug doctor` and the time the last copy finished, so you can check without the server's console. The section is as wide as the forms above it. A problem has a link to the backups guide beside it, and a server without backups says to run `sudo snug setup`, where it used to say a file was missing. The Branding and Privacy sections link to new parts of [Using Snug](docs/using-snug.md), so every section in Settings links to its guide.
- `sudo snug update` brings a server up to date: the newest release's server files from GitHub, replaced only once every one has arrived, then the new images. It never touches `snug.env` or the backup settings. Updating used to pull new images only, leaving the server's own files as they were when it was made.
- On a new server, `snug setup` asks whether you are restoring Snug from an earlier server's backups. Answer `yes` and paste the backup settings from your password manager, and it checks the bucket and restores. You used to have to put the settings file in place with a text editor on the server first.
- `docs/deploy-digitalocean.md` goes step by step from an empty DigitalOcean account to Snug at your own address, with screenshots from a real deployment. Its last section brings Snug back from the backups on a new server. The Google Workspace email guide has screenshots of each step too.
- A step-by-step guide to trying Snug on your own Mac, `docs/deploy-localhost.md`, from installing Docker Desktop onwards. It replaces the README's shorter section, which assumed Docker and Python were installed.
- [Using Snug](docs/using-snug.md), a guide to every page of the admin and what investors see, with screenshots. The deployment, backups, email, admin accounts, and NDA guides have diagrams and screenshots too.
- Screenshots in the README, in light and dark, taken from a demo company's data room in a real browser by `tests/e2e/test_screenshots.py`, so they can be retaken whenever a page changes.
- [How Snug works](docs/architecture.md), a guide to the code for developers, with diagrams in light and dark.
- Snug counts how long an investor has a workbook's page open, as it does for a page of a PDF. On People, a workbook shows one cell shaded by that time, where it used to say *Spreadsheet*. Investors are told this on the workbook's page.
- MP4 videos can be shared as documents. Investors watch them in the browser with their email address, the date, and the link drawn over the player, and Snug counts time watched in 10-second parts, shown on the document's page and on People. They can download it when the link allows downloads. Uploads are checked in the confined worker, which accepts H.264 video with optional AAC sound and refuses QuickTime and other formats with a plain message. Videos have their own size limit, `SNUG_MAX_VIDEO_MB`, 1 GB by default.
- A workbook can have a description, written on its page in the admin: plain text, kept across new versions, and shown to investors above its list of sheets.
- Excel workbooks (.xlsx) can be shared as documents, for download only. Investors see the workbook's sheets and a button to download it, and each copy has their email address, the date, and the link written into its document properties, with every sheet left exactly as uploaded. Uploads are checked in the confined worker: workbooks with macros, ActiveX, or embedded objects, old .xls files, and workbooks with passwords are refused with a plain message.
- Email through Amazon SES works from DigitalOcean: Settings accepts SES's ports 2587 (STARTTLS) and 2465 (TLS), beside 25, 465, 587, and 2525.
- *Clear email settings*, under the choice of provider in Settings and shown whenever anything is saved, forgets the Google client and mailbox or the SMTP details, and withdraws Snug's permission at Google. It needs a fresh code, and every admin is told first. Email then falls back to `snug.env`, or is off.
- Settings links each section that has a guide to it, under its heading: admin accounts, email (the Google Workspace or SMTP guide, following the choice of provider), and the NDA. The guides open in a new tab, so a half-filled form is not lost.
- Google Workspace sending can use another of the mailbox's addresses as the From address, such as one on an alias domain.
- Snug can send email through Google Workspace, as a mailbox on your domain, over the Gmail API: Settings, under Email, then [docs/email-google.md](docs/email-google.md) for the one-time Google Cloud setup. It works from DigitalOcean, which blocks Google's mail ports. Snug can only send from the mailbox, never read it, and connecting needs a fresh authenticator code.
- Email is set up in Settings, under Email, and takes effect at once, with a *Send a test* button. Passwords are never shown again. SMTP settings in `snug.env` still work until something is saved there, and `snug setup` no longer asks about email.
- First-open emails: the first time anyone opens a document through a link, every admin is emailed who, what, when, and from which country, with a link to that person on the People page. Each admin can turn their own off under Your account in Settings.
- UK-only mode: a link to a document, or a whole room, can open only for visitors in the UK, Jersey, Guernsey, and the Isle of Man. Anyone elsewhere, or anyone Snug cannot place, is turned away and sent no code, and the refusal is in the audit log. Countries come from DB-IP's free database, kept on the server; `snug doctor` and the Overview warn if it is missing or out of date.
- Housekeeping downloads DB-IP's free country database once a month and keeps it in the data folder, for UK-only links. Lookups use that copy alone, so no visitor's address leaves the server.
- The admin sign-in page shows the company's name under the heading, once a name is set in Settings or `SNUG_COMPANY_NAME`.
- `deploy/cloud-init.yaml` prepares a fresh Ubuntu 24.04 droplet: Docker, automatic security updates, a firewall, and Snug's files. Then `sudo snug setup` asks for the domain, the company's name, and the backup bucket on the server, generates the backup's encryption passwords for you to keep, and starts Snug, or restores it from an earlier server's backups. [docs/deployment.md](docs/deployment.md) walks through it.
- `sudo snug restore` brings Snug back from its backups, on the same server or a new one: the latest, or as it was at a moment in the last 7 days. Any data it replaces is moved aside and kept. A `snug` command on the server runs Snug's other commands in its container. The backup container refuses to copy into a bucket holding another Snug's backups, so a new server started before its restore cannot overwrite them.
- The admin Overview has a status line: any problem `snug doctor` would report, such as a late backup, failing housekeeping, an expiring certificate, or an email that could not be sent, or else that everything is working.
- `snug doctor` checks housekeeping, the backups, the document worker's confinement, DNS, HTTPS and the certificate's expiry, and email, by sending a code you type back. It says what to fix, and exits with status 1 if anything is wrong. [docs/deployment.md](docs/deployment.md) lists the checks.
- Continuous, encrypted backups to a storage bucket of your own: Litestream records every change to the database, and an rclone container copies it, the uploaded documents, and the branding images to the bucket every 5 minutes, encrypted on the server with a key only you hold. [docs/backups.md](docs/backups.md) explains how to set it up.
- A Docker Compose deployment: Snug behind Caddy, which gets and renews HTTPS certificates, with Snug's settings in `/opt/snug/snug.env`. [docs/deployment.md](docs/deployment.md) explains it. Both containers have read-only filesystems and no privileges they do not need, and Snug trusts a visitor's address only when it comes from Caddy, over IPv4 and IPv6.
- Your logo on investors' pages, uploaded in Settings under Branding: at the top of every page, on a light tile in dark mode, and as the icon in their browser tab. PNG or JPEG, up to 2 MB. Snug redraws it in the same confined process that reads PDFs, so nothing in the file but the picture reaches investors.
- Your accent colour on investors' pages, set in Settings under Branding with a picker and a live preview. Snug deepens or lightens the shade it uses for text and buttons where it needs to, so everything stays readable in light and dark mode, and says when it has.
- Uploads show their progress as a bar, then *Checking the file* while Snug reads it, and the button cannot be pressed twice.
- A cover message on a link: an optional note of up to 1,000 characters, shown to the investor in the About this link panel and in the email with the link.
- Replace your own authenticator in Settings, under Your account, for a new phone: your password and a code from the old app, or a recovery code. You go straight to setting up the new one, with new recovery codes, and the link is emailed in case you close the tab.
- Change your own password in Settings, under Your account, with your current password and a code from your authenticator app. You stay signed in on that browser, everywhere else is signed out, and you are emailed.
- With no NDA yet, ticking "Ask for the NDA first" on a new link, or "Ask for the NDA before anything is shown" in a room's NDA section, shows a box to write it there. Saving publishes it as version 1, as Settings would, instead of sending you away to Settings and losing what you typed.
- The People page shows how many times each person downloaded each document, and their total.
- An eye at the right of every password box shows or hides what is typed, for phones and fiddly keyboards. Password boxes also turn off autocorrect and capitalisation.
- "Forgotten your password?" on the sign-in page. It emails a link that works once, for one hour; setting the new password also needs a code from the authenticator app or a recovery code. The reply never says whether an address belongs to an admin. Without email, `snug reset-password EMAIL` does the same on the server, and is written to the audit log; Django's unaudited `changepassword` is disabled.
- The Overview: how many documents, active links, people who have visited, and visits in the last 7 days, then the ten latest visits, with a person's first open of a document marked New.
- A container image, `ghcr.io/negroni-venture-studios/snug`, for amd64 and arm64: `:edge` from `main`, and a version tag and `:latest` from each release. It runs as an unprivileged user, and its render worker is confined by Landlock.
- Reading data is deleted automatically 12 months after its link is revoked or expires, a period you can change in Settings (from 1 to 120 months). `snug housekeeping`, which the server runs every hour, does it, and also clears expired codes, sessions, setup links, and rate-limit counts.
- An Audit log tab: every admin and viewer event, newest first, filtered by kind or by email address, 100 to a page, with a CSV export of the same selection. Cells a spreadsheet would run as a formula are defused, and every export is itself recorded.
- The NDA: one agreement for the instance, written in Settings and versioned. A room, or a link to a single document, can ask for it before anything is shown. The viewer gives their name and ticks to accept; Snug records the name, email, link, time, IP address, browser, and a fingerprint of the exact text, and asks again when the agreement changes. `docs/nda.md` states that this is a record of acceptance, not a signature.
- Rooms, in the admin interface: create and rename a room, add folders (one level), and add, move, and remove documents. A document can be in any number of rooms. Every change is recorded in the audit log.
- Sharing a room: one link opens a page listing the room's documents under their folders, each opening in the reader with its own downloads and reading time. Removing a document from the room stops the link reaching it at once.
- A People page: everyone who has opened a document, most recent first, with what each of them read in the same page strips.

- How a document was read, on its page: viewers, visits, median reading time, and how many read every page, then one grid with the average time per page above a strip for each viewer, shaded by time on each page, with their visits, total time, and when they were last seen.

- Reading time: each page a viewer is shown, and how long it was in view, measured by Snug from heartbeats sent only while the tab is visible. Visits end after 30 minutes away. Each new visit is recorded in the audit log. Nothing is sent anywhere outside the instance.
- An optional link to your own privacy policy, set in Settings and shown to viewers beside the statement of what Snug records about them.

- The reader: once a viewer is in, each page is shown as an image, one at a time, with Previous, Next, the arrow keys, and a page picker. Every page is watermarked for that viewer on each request, with their email address and the date across the page and a footer with their email address, the date, and the link. Watermarks are stamped in the same confined worker that renders pages.
- Downloads: when a link allows them (the default), the reader offers the original PDF, marked on every page with the reader's faint diagonal watermark (the viewer's email address and the date) and a line with their email address, the date, and the link, and the same details in the file's properties. The text stays selectable and searchable. Each download is recorded in the audit log with a fingerprint of the exact file sent.

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
  - for links that need verification, an emailed six-digit code, with messages for a wrong address, a wrong code, and tries left;
  - for links that do not, a single *Open* button, so an email scanner visiting the link starts nothing;
  - one page for unknown, expired, and revoked links alike.

  Code requests and checks are rate limited per address, per link, and per inbox, and every step is recorded in the audit log. `SNUG_VIEWER_SESSION_DAYS` sets how long a viewer stays verified (7 days by default).
- Email through any SMTP provider, configured with `SNUG_COMPANY_NAME`, `SNUG_EMAIL_FROM`, and the `SNUG_SMTP_*` settings. `docs/email.md` covers the provider, the SPF, DKIM, and DMARC records, and DigitalOcean's blocked SMTP ports. On `localhost`, emails are written to an outbox folder instead.

- A Documents section in the admin interface:
  - a list of documents with their latest version;
  - uploading a PDF as a new document;
  - a document page showing every version and a server-rendered preview of page 1;
  - uploading new versions.

  Oversized uploads are refused from their declared size, before they are read. Uploads, new versions, and refusals are recorded in the audit log.

- Documents and versions, and the machinery behind them:
  - PDFs are checked, hashed, and stored under generated names, never their uploaded names;
  - each upload becomes the document's next version;
  - PDFs are read and rendered in a separate worker process, with no access to Snug's settings, with limits on memory, CPU time, output size, and open files, and a timeout. The worker is handed an open file rather than a path, and on Linux confines itself with Landlock and a seccomp filter that refuses sockets, so it can open no files except fonts and make no network connections;
  - Snug's own processes are non-dumpable, so other processes of the same user cannot read their memory or environment;
  - rendered pages are cached as WebP, and the cache can be deleted at any time;
  - `SNUG_MAX_UPLOAD_MB` sets the largest PDF or workbook upload (100 MB by default).
- `THIRD_PARTY_LICENCES.md`, listing the bundled PDFium components, fonts, and icons.

- Signing in to the admin interface:
  - email and password, then a code from the authenticator app or a recovery code;
  - rate limits per address and per account;
  - idle and absolute session limits;
  - every sign-in, failure, and sign-out recorded in the audit log.
- The front end from DESIGN.md revision 2, in production: the stylesheet, Geist fonts, the theme toggle (dark by default), the logo, and the sign-in pages.
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

- Repository guard rails: CI for lint, type checking, tests on Python 3.12 and 3.14, secret scanning, dependency audit, licence checks, and a software bill of materials; CodeQL; Dependabot; pre-commit hooks.
- Project documents: README, contributing guide, security policy, code of conduct, trademarks note, and agent instructions.
- DESIGN.md, recording the architecture, the agreed decisions, and the front-end design, with clickable mockups and the Snug logo in `docs/design/`.

[Unreleased]: https://github.com/Negroni-Venture-Studios/snug/compare/v1.4.0...HEAD
[1.4.0]: https://github.com/Negroni-Venture-Studios/snug/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/Negroni-Venture-Studios/snug/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/Negroni-Venture-Studios/snug/compare/v1.1.3...v1.2.0
[1.1.3]: https://github.com/Negroni-Venture-Studios/snug/compare/v1.1.2...v1.1.3
[1.1.2]: https://github.com/Negroni-Venture-Studios/snug/compare/v1.1.1...v1.1.2
[1.1.1]: https://github.com/Negroni-Venture-Studios/snug/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/Negroni-Venture-Studios/snug/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/Negroni-Venture-Studios/snug/releases/tag/v1.0.0
