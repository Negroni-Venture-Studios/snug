# Configuration

All of Snug's configuration comes from environment variables, set in one file and passed to the container. [deploy/snug.env.example](../deploy/snug.env.example) is a starting point. The standard deployment keeps the file at `/opt/snug/snug.env` on the server, as [deployment.md](deployment.md) describes.

Snug checks every setting when it starts. If any are missing or wrong, it lists every problem at once and refuses to start.

## Settings

| Variable | Required | Default | What it does |
|---|---|---|---|
| `SNUG_SECRET_KEY` | Yes | | Signs admin sessions and protects the codes emailed to investors. At least 50 random characters. Keep it secret, and never reuse it between instances. |
| `SNUG_DOMAIN` | Yes | | The host name people use to reach Snug, like `room.example.com`: no `https://`, port or path. Requests for any other host name are refused. On your own computer it can be `localhost` with a port, like `localhost:8000`, so the links Snug shows and emails include it. |
| `SNUG_DATA_DIR` | No | `/var/lib/snug` | Where Snug keeps its database and files. Must be an absolute path. Back it up, which the standard deployment does continuously. |
| `SNUG_TRUSTED_PROXIES` | No | `127.0.0.1/32, ::1/128` | The addresses of the proxy in front of Snug, Caddy in the standard deployment, as a comma-separated list of addresses or networks. The standard Compose file sets this to Caddy's own addresses, overriding any value in `snug.env`. Snug trusts the client address in `X-Forwarded-For`, and `X-Forwarded-Proto`, only on requests from these. Snug refuses to start with `0.0.0.0/0`, which would let anyone claim any address and get round the rate limits. |
| `SNUG_MAX_UPLOAD_MB` | No | `100` | The largest PDF or Office file an admin can upload, in megabytes, from 1 to 1024. Large decks with many images can reach tens of megabytes. |
| `SNUG_MAX_VIDEO_MB` | No | `1024` | The largest MP4 video an admin can upload, in megabytes, from 1 to 8192. Ten minutes of 1080p is a few hundred megabytes. Check the server's disk, and its backups, have room. |
| `SNUG_VIEWER_SESSION_DAYS` | No | `7` | How long a viewer stays signed in to a link after verifying, in days, from 1 to 90. Revoking a link ends its sessions at once, whatever this is set to. |
| `SNUG_HTTPS` | No | `true` | Snug expects to sit behind Caddy, which provides HTTPS. Can only be `false` when `SNUG_DOMAIN` is `localhost`. |
| `SNUG_DEBUG` | No | `false` | Shows detailed error pages. Can only be `true` when `SNUG_DOMAIN` is `localhost`. Never use it on a real server. |
| `SNUG_OFFICE` | No | `false` | `true` when the LibreOffice converter is installed, so Word, PowerPoint and Excel files are shown in the browser as pages. `snug setup` and `snug update --with-office` set it; see [Showing Office files in the browser](office.md). |
| `SNUG_OFFICE_DIR` | No | `/var/lib/snug-office` | The folder Snug shares with the converter, holding its `inbox` and `outbox`. Only for running Snug outside the Docker image. |
| `SNUG_UNSAFE_UNCONFINED_RENDERING` | No | `false` | Snug reads every upload in a worker confined by Linux Landlock (Linux 6.7 or later) and a seccomp filter that refuses sockets and any reach into other processes, and refuses to read the file if it cannot confine itself. `true` lets it read unconfined instead, for development on a machine without Landlock, such as macOS. A hostile file could then reach the database and every document, so never set it on a server; `snug check` warns while it is on. The Docker image on a Mac does not need it, because Docker runs a Linux kernel. |

### Email

Email is usually set up in Settings, under Email, through Google Workspace or an SMTP provider, and what is saved there is used in place of these. They are used until SMTP settings are saved or a Google mailbox is connected, and again after *Clear email settings* in Settings forgets it.

| Variable | Required | Default | What it does |
|---|---|---|---|
| `SNUG_COMPANY_NAME` | No | | Your company's name, used until one is saved in Settings under Branding, where it is usually set. At most 100 characters. |
| `SNUG_EMAIL_FROM` | When `SNUG_SMTP_HOST` is set | | The address emails come from. |
| `SNUG_SMTP_HOST` | No | | Your email provider's SMTP server. Unset, with nothing saved in Settings, email is off; on `localhost`, emails go to the outbox folder instead. On a server, Snug connects only to an SMTP server with public addresses. |
| `SNUG_SMTP_PORT` | No | `587`, or `465` with `tls` | The SMTP port: 25, 465, 587, 2465, 2525 or 2587, as in Settings, or any port on `localhost`. DigitalOcean blocks 25, 465 and 587; most providers also take 2525, and Amazon SES 2587 or 2465. |
| `SNUG_SMTP_USERNAME` | No | | The SMTP username. |
| `SNUG_SMTP_PASSWORD` | No | | The SMTP password or API key. |
| `SNUG_SMTP_SECURITY` | No | `starttls` | `starttls`, `tls` or, on `localhost` only, `none`. |

[email.md](email.md) explains how to set these up, including the DNS records that keep Snug's emails out of spam. [email-google.md](email-google.md) covers Google Workspace, which is set up in Settings only.

True and false can be written as `true`/`false`, `yes`/`no`, `on`/`off` or `1`/`0`.

### Connections out

If the server reaches the internet only through a proxy, set `HTTPS_PROXY` in the same file. Snug's own calls out, to Google for Google Workspace email and to DB-IP for the monthly country data, use it. Snug never asks the operating system for proxy settings, and SMTP connects directly.

### Docker Compose

Two more settings in the same file are read by Docker Compose, for the containers, not by Snug.

| Variable | Required | Default | What it does |
|---|---|---|---|
| `SNUG_IMAGE` | No | the release's image, such as `ghcr.io/negroni-venture-studios/snug:1.0.0` | The Snug image to run. The default is the image of the release whose files are in `/opt/snug`, which `snug update` brings up to date. |
| `SNUG_BACKUP_EVERY` | No | `300` | Seconds between copies to the backup bucket. `snug doctor` reports a problem after 15 minutes without one. |

## Generating the secret key

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(64))"
```

Changing the key signs every admin out, and any code already emailed to an investor stops working. Investors who have already verified stay signed in.

## Running Snug on your own computer

For development, create a file outside the repository, for example `~/snug-dev.env`:

```bash
SNUG_SECRET_KEY=paste-the-generated-key-here
SNUG_DOMAIN=localhost:8000
SNUG_HTTPS=false
SNUG_DEBUG=true
SNUG_DATA_DIR=/Users/you/snug-dev-data
SNUG_UNSAFE_UNCONFINED_RENDERING=true
```

The last line is for macOS only, which has no Landlock. Without it, Snug refuses to read any upload. Leave it out on Linux, and never set it on a server.

Then:

```bash
uv run snug --env-file ~/snug-dev.env serve --bind 127.0.0.1:8000
```

`snug serve` applies any pending database migrations, then starts the web server. Open <http://localhost:8000/healthz> to check it is running.

Without SMTP settings, Snug on `localhost` writes each email to the `outbox` folder inside the data folder instead of sending it, so you can read the links it would have sent.

To create an admin, run `uv run snug --env-file ~/snug-dev.env createadmin --email you@example.com`, as in [the admin accounts guide](admin-accounts.md).

To try Google Workspace sending on your own computer, add `http://localhost:8000/admin/settings/email/google/callback` to the OAuth client's redirect addresses, as [email-google.md](email-google.md) describes.

## What Snug never logs

Snug does not write a request access log, since viewer links have their access token in the URL. It never logs tokens, one-time codes, session identifiers or passwords. An unexpected error is logged with its traceback, and any link or setup token in it is replaced with `[redacted]`.
