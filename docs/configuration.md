# Configuration

All of Snug's configuration comes from environment variables, set in one file. On a server that file is `/etc/snug/snug.env` (the deployment guide sets it up), and Docker Compose loads it into the container. [deploy/snug.env.example](../deploy/snug.env.example) is a starting point.

Snug checks every setting when it starts. If any are missing or wrong, it lists every problem at once and refuses to start, rather than starting half-configured.

## Settings

| Variable | Required | Default | What it does |
|---|---|---|---|
| `SNUG_SECRET_KEY` | Yes | | Signs sessions and other tokens. At least 50 random characters. Keep it secret, and never reuse it between instances. |
| `SNUG_DOMAIN` | Yes | | The host name people use to reach Snug, like `room.example.com`: no `https://`, port or path. Requests for any other host name are refused. |
| `SNUG_DATA_DIR` | No | `/var/lib/snug` | Where Snug keeps its database and files. Must be an absolute path. Back it up, which the standard deployment does continuously. |
| `SNUG_TRUSTED_PROXIES` | No | `127.0.0.1/32, ::1/128` | The addresses of the proxy in front of Snug, Caddy in the standard deployment, as a comma-separated list of addresses or networks. Snug believes the client address in `X-Forwarded-For` only on requests from these. Never set it to everything (`0.0.0.0/0`), and Snug refuses to start if you do: anyone could then claim any address and get round the rate limits. |
| `SNUG_MAX_UPLOAD_MB` | No | `100` | The largest PDF an admin can upload, in megabytes, from 1 to 1024. Large decks with many images can reach tens of megabytes. |
| `SNUG_VIEWER_SESSION_DAYS` | No | `7` | How long a viewer stays signed in to a link after verifying, in days, from 1 to 90. Revoking a link ends its sessions at once, whatever this says. |
| `SNUG_HTTPS` | No | `true` | Snug expects to sit behind Caddy, which provides HTTPS. Can only be `false` when `SNUG_DOMAIN` is `localhost`. |
| `SNUG_DEBUG` | No | `false` | Shows detailed error pages. Can only be `true` when `SNUG_DOMAIN` is `localhost`. Never use it on a real server. |

### Email

| Variable | Required | Default | What it does |
|---|---|---|---|
| `SNUG_COMPANY_NAME` | No | | Your company's name, used in email subjects. |
| `SNUG_EMAIL_FROM` | When `SNUG_SMTP_HOST` is set | | The address emails come from. |
| `SNUG_SMTP_HOST` | No | | Your email provider's SMTP server. Unset, email is off. |
| `SNUG_SMTP_PORT` | No | `587`, or `465` with `tls` | The SMTP port. DigitalOcean blocks 25, 465 and 587. |
| `SNUG_SMTP_USERNAME` | No | | The SMTP username. |
| `SNUG_SMTP_PASSWORD` | No | | The SMTP password or API key. Never shown or logged. |
| `SNUG_SMTP_SECURITY` | No | `starttls` | `starttls`, `tls` or, on `localhost` only, `none`. |

[email.md](email.md) explains how to set these up, including the DNS records that keep Snug's emails out of spam.

True and false can be written as `true`/`false`, `yes`/`no`, `on`/`off` or `1`/`0`.

## Generating the secret key

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(64))"
```

Changing the key signs everyone out: admins and viewers will need to sign in or verify again.

## Running Snug on your own computer

For development, create a file outside the repository, for example `~/snug-dev.env`:

```bash
SNUG_SECRET_KEY=paste-the-generated-key-here
SNUG_DOMAIN=localhost
SNUG_HTTPS=false
SNUG_DEBUG=true
SNUG_DATA_DIR=/Users/you/snug-dev-data
```

Then:

```bash
uv run snug --env-file ~/snug-dev.env serve --bind 127.0.0.1:8000
```

`snug serve` applies any pending database migrations, then starts the web server. Open <http://localhost:8000/healthz> to check it is running.

Without SMTP settings, Snug on `localhost` writes each email to the `outbox` folder inside the data folder instead of sending it, so you can read the links it would have sent.

To create an admin, run `uv run snug --env-file ~/snug-dev.env createadmin`. See [admin-accounts.md](admin-accounts.md).

## What Snug never logs

Snug does not write a request access log. Viewer links carry their access token in the URL, and the security requirements forbid logging tokens, one-time codes, session identifiers or passwords.
