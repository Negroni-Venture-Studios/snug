# Showing Office files in the browser

Snug shares Word, PowerPoint, and Excel files as downloads. With LibreOffice installed on your server, investors can also read them in the browser, page by page, as they read a PDF. Each page shows their email address as a watermark, and Snug records the time they spend on it.

LibreOffice is optional, and runs in a container of its own under gVisor, with no network.

## What investors see

A presentation, document, or workbook opens in the reader, one page at a time. Each slide is a page. Each sheet of a workbook is one page, sized to the sheet, so its columns are never split across pages; a long or wide sheet is shown smaller, and a small one at its own size, on a page as wide as A4. If the link allows downloads, the *Download* button gives the file itself, with the investor's email address, the date, and the link written into its document properties.

Until a file's preview is ready, or if one could not be made, investors see a card describing the file, with your description, and a download button if the link allows downloads.

## What you see

When you upload an Office file, Snug saves it and returns at once. The document's page shows the preview being made, with how many files are ahead of it, and you can write the description and create links meanwhile. LibreOffice takes about 15 seconds a file, and converts one file at a time.

A new version of a document waits for its preview before links show it, so an investor reading pages never drops back to a card. Until then, the document's page shows which version is being prepared, and links show the previous one.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/screenshots/office-preview-dark.png">
  <img alt="A board update's page while its preview is made: a progress bar, Waiting for the converter, 2 files ahead, and a note that the description and links can be done meanwhile, beside the form to upload a new version." src="images/screenshots/office-preview-light.png">
</picture>

If a preview cannot be made, the document's page shows the reason and a *Try again* button. Investors see the file's card instead.

*Settings*, under *Office files*, shows the LibreOffice version, when the converter's image was built, and a switch for showing Office files in the browser. With it off, investors see a card describing each file. Either way, a link that allows downloads gives them the file itself.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/screenshots/settings-office-dark.png">
  <img alt="The Office files section of Settings: Working, LibreOffice 25.2.3, in an image built on 12 November 2026, with the switch Show Office files in the browser turned on." src="images/screenshots/settings-office-light.png">
</picture>

## Installing LibreOffice

The converter needs a server with 2 GB of memory, such as DigitalOcean's $12 a month droplet. On a server with 1 GB, LibreOffice can use enough memory to stop Snug, so `snug setup` and `snug update` warn you before installing it there.

`snug setup` asks whether to show Office files in the browser. On a server already running Snug:

```bash
sudo snug update --with-office
```

On a server running Snug 1.0.0, run `sudo snug update` first, then the command above. 1.0.0's `snug` command does not pass `--with-office` on, so on its own it updates Snug without LibreOffice; the update installs a `snug` command that does.

The command installs gVisor from gVisor's own packages, restarts Docker and with it Snug, and starts the converter. Snug then converts the current version of each Office document you have already uploaded, one at a time and oldest first.

`sudo snug doctor` then shows the converter working, with its LibreOffice version:

<img alt="snug doctor, ending with ok, Office converter, LibreOffice 25.2.3, in an image built on 12 November 2026." src="images/office/doctor.png">

## Keeping it up to date

Snug's converter image is rebuilt every Monday with Debian's latest LibreOffice, and `sudo snug update` installs it. *Settings* and `sudo snug doctor` show a warning when the image on your server is more than 14 days old.

## Removing it

```bash
sudo snug update --without-office
```

Investors see a card describing each Office file again. The previews stay on the server, and are shown again if you install LibreOffice later.

## What it costs in security

LibreOffice is millions of lines of C++, with import filters for hundreds of file formats. It publishes security fixes several times a year, many of them for bugs in reading files. Adding it gives an attacker a large new target, so it is optional, and it runs confined.

Only signed-in admins upload files, so nobody on the internet can send a file straight to LibreOffice. An attack needs either a stolen admin account or a malicious file that reaches you first, such as a 'revised term sheet' from someone posing as a lawyer.

## How it is confined

A file passes through five layers on its way to a preview.

1. **Snug's checks before conversion.** Snug's checker reads every Office file in its confined document worker, whether or not LibreOffice is installed. It refuses to take a file with macros, ActiveX controls, or objects embedded from other programs; one that fetches a template, picture, or object from another computer when it opens; a Word field that fetches content or starts another program; a Word document with another inside it, such as a web page, to import when it opens; a workbook linked to another program; or a file in Office's older binary formats inside it. An Office file inside another, such as the workbook that holds a chart's data, is checked in the same way, and one nested any deeper is refused. The upload is refused with the reason, so you can save the file without it and upload it again. LibreOffice is then told which import filter to use, from the kind the checker found, so it never chooses one from the file's contents.
2. **A container of its own.** The converter runs under gVisor, which puts its own kernel between LibreOffice and the server's. The container has no network, a read-only file system, no Linux capabilities, and its own user, and is limited to 1 GB of memory, 256 processes, and one CPU. It holds no Snug settings or secrets. It shares two folders with Snug: a read-only one holding the file to convert, and a writable one for the PDF.
3. **A restart for every file.** The converter converts a single file and exits, and Docker restarts it for the next, so no process or temporary file is kept between conversions.
4. **LibreOffice's own limits.** LibreOffice gets a fresh profile for each file, with macros and link updates turned off, a minimal environment, limits on CPU time and file size, two minutes to finish, and a seccomp filter that refuses network sockets and any reach into another process. If the filter cannot be applied, the file is not converted.
5. **Snug's checks after conversion.** Snug reads the converter's output without following symbolic links, and takes only the PDF for the file it sent. Its confined worker then reads that PDF before any investor sees a page.

An attacker who took over LibreOffice through a file would have access only to that file and its PDF. The container has no network, no Snug database, no other documents, and no secret key. [What Snug protects against](security.md) covers the rest of Snug.
