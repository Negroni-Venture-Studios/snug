# What Snug protects against

Snug controls who can open each document, and marks what they see with their email address. It cannot stop someone who can open a document from keeping a copy of it. The marks let you trace a leaked copy back to the person it was shared with.

## Who can open a document

Each link is for one person. Its address holds a random token, which Snug shows you once and stores only as a hash, so nobody can guess a link or read one back out of the database.

With **Require email verification** ticked, as it is unless you untick it, the visitor must also type a code that Snug emails to the link's address. A forwarded link is then useless to anyone without access to that inbox. With it unticked, anyone who has the link can open it.

Revoking a link, or letting it expire, stops it at once, including on pages already open. A room's documents are checked on every request, so a document removed from a room stops opening through that room's links straight away.

Snug cannot tell who is at the keyboard. Someone the investor lets use their inbox, or their open browser, can read what the investor can.

## Guessed codes and passwords

An investor's code is six digits, works once, for ten minutes, and five wrong tries use it up. Asking for codes is limited for each link, email address and internet address, so someone holding a forwarded link cannot keep guessing.

Signing in to the admin pages takes a password and a code from an authenticator app. After 20 wrong passwords from one address, 30 against one account, or 10 wrong codes, signing in pauses for 15 minutes ([every limit](admin-accounts.md#signing-in)).

## Uploaded files

Snug reads every uploaded PDF, Office file and video in a separate worker process, confined so that it cannot open other files, connect to the network or reach Snug's other processes, with a time and a memory limit. If the worker cannot confine itself, Snug refuses to read the file. A file made to attack the PDF reader would run inside that confinement.

The worker refuses a Word, PowerPoint or Excel file with macros, ActiveX controls or embedded objects, one that fetches a template, picture or object from another computer when it opens, a Word field that does the same or starts another program, and a workbook linked to another program. If you install LibreOffice to show Office files in the browser, it runs in a container of its own under gVisor, with no network; [Showing Office files in the browser](office.md) sets out what it costs and how it is confined.

## Watermarks and stamps

Every page an investor reads in the browser shows their email address and the date, repeated diagonally across the page, and a line at the foot with the link's reference. A PDF or Office file download is stamped for its reader too, and the audit log records every download with a hash of the exact file sent.

| What they get | How it is marked | What the mark cannot stop |
|---|---|---|
| A page in the browser | Their email address and the date across the page, and a footer line | A photograph or a screenshot, which can then be cropped or edited |
| A PDF download | The same marks on every page, as an image layer, and their details in the document's properties | Someone with a PDF editor removing the layer and the properties, or copying the text out |
| A Word, PowerPoint or Excel download | Their details in the file's comments and three custom properties; every sheet, slide and paragraph stays exactly as uploaded | Copying the contents into a new file, or editing the properties |
| A video | Their email address and the date drawn over the player | The browser's developer tools hiding it; Safari on iPhone playing in its own full screen without it; saving the stream, even with downloads off |
| A video download | None: it is the original file | Anything. Every download is the same file, so its hash cannot show whose copy it was |

The hash in the audit log matches only an unchanged copy. Once a file has been edited, Snug cannot tell whose it was.

Pages stamped for an investor are kept on the server until the end of the day, so a page they return to is not stamped again, and the first pages are stamped while they open the link. Every request for one is authorised first, revoking the link deletes them, and they are not backed up.

Turning off downloads on a link stops the download button. The investor can still screenshot what they can see, and save a video as it plays.

## The NDA record

When an investor accepts the NDA, Snug records the name they type, their email address, the link, the time, their IP address and browser, and a SHA-256 hash of the exact text they accepted. Each acceptance is kept for that link and that version, and editing the NDA asks everyone to accept the new version.

This is a record of acceptance, not a signature. Snug knows the email address only as well as the link's verification checks it, and has no other check on who typed the name. Whether a record like this binds anyone is a question for your lawyer.

## UK-only mode

A UK-only link or room opens only for visitors whose internet address DB-IP's data places in the UK, Jersey, Guernsey or the Isle of Man. An address it cannot place is refused, and so is every visitor while the data is missing.

A VPN gets round it, and so does any visitor using a computer in the UK. It checks where a connection comes from, and is not export control.

## Your server and your backups

Your documents, the database and the record of who read them stay on your server. With backups set up, as `snug setup` asks, they are also copied to your own bucket, encrypted on the server before they leave it. Without them, losing the server loses everything. The backup settings in your password manager are the only way to read the backups, so whoever holds them can read everything in them.

Snug is served only over HTTPS, with a certificate from Let's Encrypt that Caddy renews, and tells browsers to use nothing else for a year.

Anyone with root on the server can read everything on it, including the documents, the database, and the email password or Google permission saved in Settings. So can your hosting provider. Keep the server's root password, and the console it opens, to the people who run Snug.

Every admin has the same rights, and signs in with a password and a code from an authenticator app. Adding or removing an admin, or changing how email is sent, needs a fresh code, and once email is set up, Snug emails every admin about it.

## What Snug records about investors

Snug records each visitor's email address, IP address and country, and how long they spent on each page. Beside every document, investors are told that you will see when they open it and how long they spend on each page; the NDA page also tells them their IP address is recorded. The reading data is deleted a set time after their link ends, 12 months unless you change it in Settings; the audit log is kept.

## Reporting a security problem

To report a vulnerability in Snug, follow the [security policy](../SECURITY.md).
