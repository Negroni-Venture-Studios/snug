# Backups

The standard deployment backs Snug up continuously to a storage bucket of your own, such as DigitalOcean Spaces. Everything is encrypted on your server before it is uploaded, with a key only you hold: neither Negroni nor the bucket's provider can read it.

## What is backed up

| What | How often | History kept |
|---|---|---|
| The database: admins, links, rooms, the audit log, NDA acceptances, reading data and settings | Litestream records every change within a second; the bucket gets a copy every 5 minutes | Any moment in the last 7 days |
| Uploaded documents (`originals/`) | Every 5 minutes | A deleted or replaced file is kept for 30 days |
| Your logo and tab icon (`branding/`) | Every 5 minutes | As documents |

The page images Snug renders, and the files it keeps while uploading, are not backed up: Snug rebuilds them as needed.

If the server is lost, you lose at most the last 5 minutes of changes.

The bucket mirrors the server, but nothing is deleted from it straight away: whatever a copy would delete or overwrite, including the database replica's own files, is moved to `deleted/<date>/` in the bucket and kept for 30 days. So if the server's data is wiped by mistake, the backups survive it for a month.

The server holds the bucket's keys, so someone who takes control of it can delete the backups. Turning on versioning for the bucket, where your provider offers it, keeps deleted files out of their reach for as long as you choose.

## How it works

Two containers beside Snug do the work, as [deployment.md](deployment.md) describes:

- **Litestream** copies every change to the database into `backup/database` in Snug's data volume, within a second. Its settings are in `litestream.yml`.
- **backup** runs rclone every 5 minutes. It copies `backup/database`, `originals/` and `branding/` to the bucket through an rclone crypt remote, which encrypts each file's name and contents (XSalsa20 and Poly1305) before it leaves the server. Its script is `backup.sh`.

Only the backup container sees the bucket's keys and the encryption password, in `/opt/snug/backup/rclone.conf`. Snug itself never does, so a fault in Snug cannot reach the backups. The backup container can reach the internet, but not Snug.

## Setting up

You need a bucket and a key that can read and write it. On DigitalOcean:

1. In the control panel, create a Spaces bucket in the region nearest your server. Keep it private, with no CDN.
2. Under *Spaces Object Storage*, *Access Keys*, create a key limited to that bucket, with read and write access. Note the access key and its secret.

Then, on the server:

```bash
cd /opt/snug
sudo mkdir -p backup
sudo curl -fsSL -o backup/rclone.conf https://raw.githubusercontent.com/Negroni-Venture-Studios/snug/main/deploy/rclone.conf.example
```

Generate the two encryption passwords. Each command prints one, already in the form `rclone.conf` takes:

```bash
sudo docker compose run --rm --no-deps --entrypoint rclone backup obscure "$(openssl rand -base64 32)"
sudo docker compose run --rm --no-deps --entrypoint rclone backup obscure "$(openssl rand -base64 32)"
```

Edit `backup/rclone.conf`:

- under `[bucket]`, the access key, its secret, and your region's endpoint (`lon1`, `ams3`, `nyc3` and so on);
- under `[snug-backup]`, your bucket's name in place of `your-bucket`, and the two printed values as `password` and `password2`.

Make it readable by the backup container alone, and start it:

```bash
sudo chown 10001:10001 backup/rclone.conf
sudo chmod 600 backup/rclone.conf
sudo docker compose up --detach
```

**Copy the whole of `rclone.conf` into your password manager now.** The two passwords are the only way to read the backups. If the server is lost and you do not have them, the backups are useless.

## Checking

`snug doctor` reports a problem if nothing has been copied to the bucket for 15 minutes, with the reason when the backup container recorded one ([deployment.md](deployment.md)).

Within 5 minutes of starting, the backup container records its first complete copy:

```bash
sudo docker compose exec snug cat /var/lib/snug/backup/last-success
```

This prints the time of the last complete copy, in UTC. If the last attempt failed, `backup/problem` says why, and the backup container's log has the details:

```bash
sudo docker compose exec snug cat /var/lib/snug/backup/problem
sudo docker compose logs backup
```

To see what is in the bucket, with the names decrypted:

```bash
sudo docker compose exec backup rclone lsf -R snug-backup:
```

## Restoring

Snug has no restore command yet; it is the next piece of the deployment work in [DESIGN.md](../DESIGN.md), section 4.
