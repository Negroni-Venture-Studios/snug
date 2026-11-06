# Deployment

Snug runs on one Linux server as four containers under Docker Compose:

- Snug itself;
- [Caddy](https://caddyserver.com), which gets HTTPS certificates from Let's Encrypt, renews them, and passes every request to Snug;
- Litestream and rclone, which back Snug up, encrypted, to a storage bucket of your own, as [backups.md](backups.md) describes.

## What you need

- A Linux server with Docker Engine and its Compose plugin. A droplet with 1 GB of memory is enough for one company.
- A domain name, such as `room.example.com`, whose DNS `A` record points at the server, and its `AAAA` record too if the server has IPv6.
- Ports 80 and 443 open to the internet. Caddy needs port 80 to prove to Let's Encrypt that it controls the domain, and redirects everything else on it to HTTPS.
- An email provider's SMTP details, so Snug can send links and codes. See [email.md](email.md).
- A storage bucket for the backups, such as DigitalOcean Spaces.

## Setting it up

Everything lives in `/opt/snug`:

```bash
sudo mkdir -p /opt/snug && cd /opt/snug
sudo curl -fsSLO https://raw.githubusercontent.com/Negroni-Venture-Studios/snug/main/deploy/compose.yaml
sudo curl -fsSLO https://raw.githubusercontent.com/Negroni-Venture-Studios/snug/main/deploy/Caddyfile
sudo curl -fsSLO https://raw.githubusercontent.com/Negroni-Venture-Studios/snug/main/deploy/litestream.yml
sudo curl -fsSLO https://raw.githubusercontent.com/Negroni-Venture-Studios/snug/main/deploy/backup.sh
sudo curl -fsSLO https://raw.githubusercontent.com/Negroni-Venture-Studios/snug/main/deploy/restore.sh
sudo curl -fsSLO https://raw.githubusercontent.com/Negroni-Venture-Studios/snug/main/deploy/snug
sudo install -m 755 snug /usr/local/bin/snug
sudo curl -fsSL -o snug.env https://raw.githubusercontent.com/Negroni-Venture-Studios/snug/main/deploy/snug.env.example
sudo chmod 600 snug.env
sudo ln -s snug.env .env
```

Fill in `snug.env` as [configuration.md](configuration.md) describes: at least `SNUG_SECRET_KEY` and `SNUG_DOMAIN`, and the email settings. Then start Snug and create the first admin:

```bash
sudo docker compose up --detach
sudo snug createadmin --email you@example.com
```

[admin-accounts.md](admin-accounts.md) explains the rest of the admin's setup. Then set up the backups, as [backups.md](backups.md) describes; until then, the backup container records that they are not set up.

`.env` is a link to `snug.env`. Compose reads `.env` to fill in the domain for Caddy, and gives the whole of `snug.env` to Snug alone, so Caddy never sees the secret key or the SMTP password.

Compose treats `$` in `snug.env` as the start of a variable. Wrap any value containing `$`, such as some SMTP passwords, in single quotes: `SNUG_SMTP_PASSWORD='pa$$word'`.

## How the containers are set up

| | Snug | Caddy |
|---|---|---|
| Image | `ghcr.io/negroni-venture-studios/snug:edge`, a test build of the latest code; set `SNUG_IMAGE` in `.env` to use another | `caddy`, pinned by digest |
| Data | The `snug-data` volume, mounted at `/var/lib/snug` | The `caddy-data` volume, holding its certificates |
| Ports | None on the server; only Caddy can reach it | 80 and 443, with 443 over UDP for HTTP/3 |
| Filesystem | Read-only, apart from the data folder and `/tmp` | Read-only, apart from its volumes and `/tmp` |
| Privileges | Runs as the unprivileged `snug` user, with no Linux capabilities | Only the capability to listen on ports below 1024 |

Neither can gain privileges once started (`no-new-privileges`). Litestream and the backup container are confined the same way, and run as Snug's user, which owns the data volume. Litestream has no network at all; the backup container is on a network of its own, from which it can reach the bucket but not Snug. Docker keeps at most 30 MB of each container's output.

Caddy writes no access log, because investors' links carry their token in the URL.

### Client addresses

Rate limits and the audit log depend on knowing each visitor's real address. Caddy has fixed addresses on the Compose network, `172.30.83.2` and `fd53:6e75:6700::2`, and Snug believes `X-Forwarded-For` only from those: the Compose file sets `SNUG_TRUSTED_PROXIES` itself, whatever `snug.env` says. Caddy ignores any `X-Forwarded-For` a visitor sends, and passes on the address the connection really came from.

IPv6 is turned on for the Compose network. Without it, Docker would relay visitors arriving over IPv6 through a proxy of its own, and they would all reach Caddy from one address and share one set of rate limits.

## Checking it works

`snug doctor` checks everything an operator would want to know, and says what to fix:

```bash
sudo snug doctor --email you@example.com
```

| Check | Fine when |
|---|---|
| Housekeeping | `snug housekeeping`, which Snug runs every hour to clear expired codes, sessions and old reading data, has completed in the last 3 hours |
| Backups | The backup container has copied everything to the bucket in the last 15 minutes ([backups.md](backups.md)) |
| PDF worker | A fresh worker confines itself with Landlock ABI 4 or later and the socket filter, so it is allowed to read documents |
| DNS | The domain resolves; the report lists the addresses it points to |
| HTTPS | The domain answers Snug's health check over HTTPS, with a valid certificate that has more than 14 days left |
| Email | Email is set up; with `--email`, a code sent to that address is typed back |

The admin Overview shows the same problems, so a founder sees them without running a command. It checks housekeeping, the backups and the email settings on each visit; housekeeping runs the PDF worker, DNS and HTTPS checks every hour, and a failed email is recorded when it fails.

Snug cannot read your inbox, so the email check sends a six-digit code and asks you to type it in. Without `--email`, `snug doctor` asks for an address, or skips the email test when it is not run in a terminal. It exits with status 1 if anything is a problem.

## Running commands

The `snug` command installed in `/usr/local/bin` runs Snug's commands inside its container, from `/opt/snug`:

```bash
sudo snug createadmin --email someone@example.com
sudo snug reset-2fa someone@example.com
sudo snug check
sudo snug doctor
```

`sudo snug restore` is the exception: it runs on the server itself, because it stops and starts the containers, as [backups.md](backups.md) explains.

## Updating

```bash
cd /opt/snug
sudo docker compose pull
sudo docker compose up --detach
```

Snug applies any database migrations when it starts.
