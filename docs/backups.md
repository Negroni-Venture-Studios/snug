# Backups

The standard deployment backs Snug up continuously to a storage bucket of your own, such as DigitalOcean Spaces. Everything is encrypted on your server before it is uploaded, with a key only you hold: neither Negroni nor the bucket's provider can read it.

## What is backed up

| What | How often | History kept |
|---|---|---|
| The database: admins, links, rooms, the audit log, NDA acceptances, reading data and settings | Litestream records every change within a second; the bucket gets a copy every 5 minutes | Any moment in the last 7 days can be restored |
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

`snug setup` asks for the bucket and its key, generates the encryption passwords, and shows you the finished settings to keep, as [deployment.md](deployment.md) describes. Before running it, create the bucket and the key. On DigitalOcean:

1. In the control panel, create a Spaces bucket in the region nearest your server. Keep it private, with no CDN.
2. Under *Spaces Object Storage*, *Access Keys*, create a key limited to that bucket, with read and write access. Note the access key and its secret.

**Copy the settings `snug setup` shows you into your password manager.** They are in `/opt/snug/backup/rclone.conf`, and hold the bucket's key and the two encryption passwords. The passwords are the only way to read the backups: if the server is lost and you do not have them, the backups are useless.

### By hand

For a bucket elsewhere, write `rclone.conf` yourself from [deploy/rclone.conf.example](../deploy/rclone.conf.example) before running `snug setup`, which then uses it as it is. rclone supports most S3-compatible stores. Each of the two encryption passwords comes from:

```bash
sudo docker compose run --rm --no-deps --entrypoint rclone backup obscure "$(openssl rand -base64 32)"
```

Make the file readable by the backup container alone:

```bash
sudo chown 10001:10001 /opt/snug/backup/rclone.conf
sudo chmod 600 /opt/snug/backup/rclone.conf
```

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

`snug restore` brings everything back from the bucket: the database, the uploaded documents and the branding images. It works on the same server, after a mistake, or on a new one, after losing the old.

On a new server, create it with cloud-init as [deployment.md](deployment.md) describes. Before running `snug setup`, put the copy of `rclone.conf` from your password manager in `/opt/snug/backup/`. `snug setup` asks for the domain and email settings, finds the backups, and restores them. Then sign in to check, and run `sudo snug doctor`.

On the same server:

```bash
sudo snug restore
```

It stops Snug and its backups, fetches everything from the bucket, rebuilds the database with Litestream, and starts them again.

To go back to an earlier moment within the last 7 days, give the time in UTC:

```bash
sudo snug restore --at 2026-11-05T14:00:00Z
```

If the server already has a database, `snug restore` asks you to type `replace` first. Nothing is deleted: the data it replaces is moved to a folder named `previous-` and the time, in the data volume.

Each Snug has a random identity, kept in the data volume and, from the first backup, in the bucket. The backup container never copies into a bucket that holds another Snug's backups. If a new server is started before it is restored, its backups stop with that problem, rather than replacing the real backups with an empty database, until `snug restore` brings the bucket's data, and its identity, to the server.

Documents deleted in the last 30 days are not restored with the rest. They are in the bucket under `deleted/` and the date they were deleted, and can be copied back by hand:

```bash
sudo docker compose exec backup rclone lsf -R snug-backup:deleted
```
