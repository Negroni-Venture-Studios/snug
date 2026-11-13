# Backups

The standard deployment backs Snug up continuously to a storage bucket of your own, such as DigitalOcean Spaces. Everything is encrypted on your server, with your own key, before it is uploaded.

## What is backed up

| What | How often | History kept |
|---|---|---|
| The database: admins, links, rooms, the audit log, NDA acceptances, reading data, and settings, including how email is sent | Litestream records every change within a second; the bucket gets a copy every 5 minutes | Any moment in the last 7 days can be restored |
| Uploaded documents (`originals/`): PDFs, Office files, and videos | Every 5 minutes | A deleted or replaced file is kept for 30 days |
| Your logo and tab icon (`branding/`) | Every 5 minutes | As documents |

The page images Snug renders and stamps, Office files' previews, the files it keeps while uploading, and DB-IP's country data are not backed up, since Snug rebuilds or fetches them again as needed.

Videos are usually the largest files, often hundreds of megabytes each, so they account for most of the bucket's growth. A replaced or deleted video stays in the bucket for 30 days too.

If the server is lost, you lose at most the last 5 minutes of changes.

The bucket mirrors the server, but nothing is deleted from it straight away. Whatever a copy would delete or overwrite, including the database replica's own files, is moved to `deleted/<date>/` in the bucket and kept for 30 days. So if the server's data is wiped by mistake, the backups survive it for a month.

The server holds the bucket's keys, so someone who takes control of it can delete the backups. Versioning, where your provider offers it, keeps earlier versions of deleted or replaced files, which helps after a mistake, but the same keys can usually delete those versions too. For copies that outlast the server being taken over, use your provider's object lock if it has one, or copy the bucket from another computer with keys of its own.

## How it works

Two containers beside Snug make the backups:

- **Litestream** copies every change to the database into `backup/database` in Snug's data volume, within a second. Its settings are in `litestream.yml`.
- **backup** runs rclone every 5 minutes. It copies `backup/database`, `originals/`, and `branding/` to the bucket through an rclone crypt remote, which encrypts each file's contents (XSalsa20 and Poly1305) and its name (AES-256) before it leaves the server. Its script is `backup.sh`. `SNUG_BACKUP_EVERY` in `snug.env` sets the interval in seconds, 300 unless set; `snug doctor` still reports a problem after 15 minutes without a copy.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/diagrams/backups-dark.svg">
  <img alt="Litestream copies every database change to a replica on the same volume, and every 5 minutes rclone encrypts the replica, the uploads, and the branding into your bucket, keeping anything it would overwrite or delete for 30 days. sudo snug restore sets the current data aside, copies everything back, and rebuilds the database at any moment in the last 7 days." src="images/diagrams/backups-light.svg">
</picture>

Only the backup container sees the bucket's keys and the encryption passwords, in `/opt/snug/backup/rclone.conf`, so a fault in Snug cannot reach the backups. The backup container can reach the internet, but not Snug.

## Setting up

`snug setup` asks for the bucket and its key, generates the encryption passwords, and shows you the finished settings to keep, as [deployment.md](deployment.md) describes. Before running it, create the bucket and the key. On DigitalOcean:

1. In the control panel, create a Spaces bucket in the region nearest your server. Keep it private, with no CDN.
2. Under *Spaces Object Storage*, *Access Keys*, create a key limited to that bucket, with *Read/Write/Delete* access. Note the access key and its secret.

**Copy the settings `snug setup` shows you into your password manager.** They are in `/opt/snug/backup/rclone.conf`, and hold the bucket's key and the two encryption passwords. The passwords are the only way to read the backups.

### By hand

For a bucket elsewhere, write `rclone.conf` yourself from [deploy/rclone.conf.example](../deploy/rclone.conf.example) before running `snug setup`, which then uses it as it is. rclone supports most S3-compatible stores. Each of the two encryption passwords comes from this command, run in `/opt/snug` like the other `docker compose` commands here:

```bash
sudo docker compose run --rm --no-deps --entrypoint rclone backup obscure "$(openssl rand -base64 32)"
```

Make the file readable by the backup container alone:

```bash
sudo chown 10001:10001 /opt/snug/backup/rclone.conf
sudo chmod 600 /opt/snug/backup/rclone.conf
```

## Checking

`snug doctor` reports a problem as soon as a copy to the bucket fails, with the reason the backup container recorded, and also if nothing has been copied for 15 minutes ([deployment.md](deployment.md)). Settings shows the same under **Backups**, with the time the last complete copy finished.

Within 5 minutes of starting, the backup container records its first complete copy:

```bash
sudo docker compose exec snug cat /var/lib/snug/backup/last-success
```

This prints the time of the last complete copy, in UTC. If the last attempt failed, `backup/problem` has the reason, and the backup container's log has the details:

```bash
sudo docker compose exec snug cat /var/lib/snug/backup/problem
sudo docker compose logs backup
```

To see what is in the bucket, with the names decrypted:

```bash
sudo docker compose exec backup rclone lsf -R snug-backup:
```

## Restoring

`snug restore` brings everything back from the bucket: the database, the uploaded documents, and the branding images. Run it on the same server after a mistake. On a new server, `snug setup` runs it.

On DigitalOcean, [Putting Snug on DigitalOcean](deploy-digitalocean.md#bringing-snug-back-on-a-new-server) goes through it step by step. On another provider, create the new server with cloud-init as [deployment.md](deployment.md) describes, and run `sudo snug setup`. After the domain, your company's name, and whether to show Office files in the browser, it asks whether you are restoring Snug from an earlier server's backups. Type `yes`, then paste the backup settings saved in your password manager, from the first line, `# Snug's backups`, to the last, `password2 = ...`. Setup checks the bucket holds backups and restores them. Then sign in to check, and run `sudo snug doctor`.

The email settings come back with the database, including a Google Workspace connection, which goes on sending. If the new server has a different domain, add its redirect address to the Google client too, as [email-google.md](email-google.md) describes, so the mailbox can be connected again when it needs to be.

On the same server:

```bash
sudo snug restore
```

It stops Snug and its backups, fetches everything from the bucket, rebuilds the database with Litestream, and starts them again.

To go back to an earlier moment within the last 7 days, give the time in UTC:

```bash
sudo snug restore --at 2026-11-05T14:00:00Z
```

If the server already has a database, `snug restore` asks you to type `replace` first. The data it replaces, with the page images rendered and stamped from it, is kept in a folder in the data volume named `previous-` and the time.

Each Snug has a random identity, kept in the data volume and, from the first backup, in the bucket. The backup container never copies into a bucket that holds another Snug's backups. If a new server is started before it is restored, its backups stop with that problem until `snug restore` brings the bucket's data, and its identity, to the server.

Documents deleted in the last 30 days are not restored with the rest. They are in the bucket under `deleted/` and the date they were deleted, and can be copied back by hand:

```bash
sudo docker compose exec backup rclone lsf -R snug-backup:deleted
```
