# Deployment

Snug runs on one Linux server as four containers under Docker Compose:

- Snug itself;
- [Caddy](https://caddyserver.com), which gets HTTPS certificates from Let's Encrypt, renews them, and passes every request to Snug;
- Litestream and rclone, which back Snug up, encrypted, to a storage bucket of your own, as [backups.md](backups.md) describes.

## What you need

- A server running Ubuntu 24.04 LTS. A DigitalOcean droplet with 1 GB of memory is enough for one company.
- A domain name, such as `room.example.com`, whose DNS `A` record points at the server, and its `AAAA` record too if the server has IPv6.
- An email provider's SMTP details, so Snug can send links and codes. See [email.md](email.md).
- A storage bucket for the backups, such as DigitalOcean Spaces, and a key for it. See [backups.md](backups.md).

## Setting it up

1. On DigitalOcean, create a droplet running Ubuntu 24.04 LTS with IPv6 turned on, and paste the whole of [deploy/cloud-init.yaml](../deploy/cloud-init.yaml), which holds no secrets, into *User data* under *Advanced options*.
2. Point the domain's `A` record at the server's address, and its `AAAA` record at its IPv6 address.
3. Wait a few minutes for cloud-init to finish, then sign in with SSH and run:

   ```bash
   sudo snug setup
   ```

cloud-init installs Docker from Docker's own repository, Ubuntu's automatic security updates, a firewall that lets in only SSH, HTTP and HTTPS, a 1 GB swap file, and Snug's files in `/opt/snug`, with the `snug` command in `/usr/local/bin`. It does not start Snug.

`snug setup` asks for the domain, the company's name, the email settings and the backup bucket, typed on the server so that no secret is ever stored with DigitalOcean. It generates the secret key and the backup's encryption passwords, shows you the backup settings to keep in your password manager, starts Snug, and asks for the first admin's address, password and authenticator. [admin-accounts.md](admin-accounts.md) explains the rest of the admin's setup. You can run it again to change an answer.

If `/opt/snug/backup/rclone.conf` is already there, copied from your password manager, and its bucket holds backups, `snug setup` restores them instead of starting afresh. [backups.md](backups.md) explains when you would do this.

### On another server

On any Linux server with Docker Engine and its Compose plugin, fetch the same files and run `snug setup`:

```bash
sudo mkdir -p /opt/snug/backup && cd /opt/snug
for file in compose.yaml Caddyfile litestream.yml backup.sh restore.sh setup.sh snug; do
  sudo curl -fsSLO "https://raw.githubusercontent.com/Negroni-Venture-Studios/snug/main/deploy/$file"
done
sudo install -m 755 snug /usr/local/bin/snug
sudo snug setup
```

Open ports 80 and 443 to the internet. Caddy needs port 80 to prove to Let's Encrypt that it controls the domain, and redirects everything else on it to HTTPS.

### The settings file

`snug setup` writes Snug's settings to `/opt/snug/snug.env`, which [configuration.md](configuration.md) describes in full. To change one by hand, edit the file and run `sudo docker compose up --detach` in `/opt/snug`.

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

Once a month, housekeeping downloads [DB-IP](https://db-ip.com)'s free country database, about 5 MB, for UK-only links. It is the only download Snug makes by itself, and it sends DB-IP nothing about your visitors: Snug looks addresses up in its own copy.

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
| GeoIP | When anything is UK-only: DB-IP's country data is this month's or last month's |

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
