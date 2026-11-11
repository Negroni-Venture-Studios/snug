# Putting Snug on DigitalOcean

Setting Snug up takes about an hour, most of it waiting. Every step happens in your web browser: DigitalOcean's website has a window onto your server where you type the few commands needed.

## Before you start

You need:

- **A DigitalOcean account**, with a payment card added. Sign up at [digitalocean.com](https://www.digitalocean.com).
- **A domain name you own**, such as `yourcompany.com`, and the login for the company you bought it from, where its DNS settings are.
- **A password manager**, such as 1Password or Bitwarden. You will save four things in it as you go.
- **An authenticator app on your phone**, such as 1Password, Google Authenticator or Authy.

Decide now on the address investors will use. A part of your own domain works best, such as `room.yourcompany.com`. This guide calls it **your Snug address**.

Snug costs what DigitalOcean charges for the server and the storage, about $11 a month at the time of writing: $6 for the server and $5 for the backup storage.

## 1. Create the storage for your backups

Snug copies everything to a storage bucket every five minutes, so nothing is lost if the server is.

1. Sign in to DigitalOcean at [cloud.digitalocean.com](https://cloud.digitalocean.com).
2. Click **Create** at the top right, then **Spaces Object Storage**.
3. Under **Choose a datacenter region**, choose **London (LON1)**.
4. Leave **Enable CDN** unticked.
5. Under **Name your bucket**, type a name of your own, such as `yourcompany-snug-backups`. Names are shared by every DigitalOcean customer, so add your company's name to make it unique.
6. Click **Create a Spaces Bucket**.

In your password manager, make a new item called **Snug backups**. Write in it the region, `lon1`, and the bucket's name.

## 2. Create a key for the storage

Snug needs a key to put files in the bucket.

1. In the menu on the left, click **Spaces Object Storage**, then the **Access Keys** tab.
2. Click **Create Access Key**.
3. Under **Select access scope**, choose **Limited Access**, then tick your new bucket and choose **Read/Write/Delete** beside it.
4. Under **Name your access key**, type `snug`.
5. Click **Create Access Key**.

DigitalOcean shows two values: the **Access Key ID** and the **Secret Key**. The secret is shown only once. Copy both into the **Snug backups** item in your password manager before you leave the page.

## 3. Choose how you will sign in to the server

The server needs a way to know it is you. There are two, and either works with the rest of this guide.

- **(A) A password.** Nothing to set up on your computer. The server accepts the password from anywhere on the internet, so it must be long and random, and kept in your password manager.
- **(B) An SSH key.** A pair of files made on your computer; the server lets in only someone holding the private one. Safer, and lets you connect from Terminal as well as the browser, but it takes about ten minutes more.

If you are not sure, choose A. You can add a key later.

### (A) A password

Ask your password manager to generate a password of at least 24 characters, and save it as a new item called **Snug server**. You will paste it in the next step.

### (B) An SSH key

On a Mac, open Terminal (press ⌘ Space, type `Terminal`, press Return), and type:

```bash
ssh-keygen -t ed25519 -C snug
```

Press Return when it asks where to save the key. When it asks for a passphrase, type a new one, save it in your password manager as **Snug server**, and type it again. Then copy the public half of the key:

```bash
pbcopy < ~/.ssh/id_ed25519.pub
```

You will paste it in the next step.

## 4. Create the server

DigitalOcean calls a server a droplet.

1. Click **Create** at the top right, then **Droplets**.
2. Under **Choose Region**, choose **London**.
3. Under **Choose an image**, click **Ubuntu**, and in its version list choose **24.04 (LTS) x64**.
4. Under **Choose Size**, choose **Basic**, then **Regular**, then the **$6/mo** option, with 1 GB of memory and a 25 GB disk. If you will share videos, choose a larger disk: each video can be hundreds of megabytes.
5. Under **Choose Authentication Method**:
   - **(A)** choose **Password**, and paste the password from your password manager into **Create root password**;
   - **(B)** choose **SSH Key**, click **New SSH Key**, paste the key into **SSH key content**, type `My computer` under **Name**, and click **Add SSH Key**.
6. Click **Advanced Options** to open it, and tick **Enable IPv6**.
7. Tick **Add Initialization scripts**. Open [Snug's setup script](https://raw.githubusercontent.com/Negroni-Venture-Studios/snug/main/deploy/cloud-init.yaml) in a new tab, select all of it (⌘A on a Mac, Ctrl+A on Windows), copy it, and paste it into the box.
8. Under **Hostname**, type `snug`.
9. Click **Create Droplet** at the bottom.

The server takes a minute to start, then about five more minutes to install what Snug needs. Carry on with the next step meanwhile.

## 5. Point your Snug address at the server

1. Click **Droplets** in the menu on the left, then **snug**.
2. At the top of its page are two addresses. **ipv4** looks like `203.0.113.25`. **ipv6** is longer, and looks like `2a03:b0c0:1:d0::1a2b:3001`. Click each one to copy it.

Now go to the website of the company you bought your domain from, and find its **DNS** settings for your domain. Add two records:

| Type | Name | Value |
|---|---|---|
| `A` | the first part of your Snug address, such as `room` | the server's ipv4 address |
| `AAAA` | the same, such as `room` | the server's ipv6 address |

Leave any **TTL** setting as it is. The change can take from a few minutes to an hour to reach everyone.

## 6. Open the server's console

The console is a window onto the server, inside your browser.

1. Go back to the **snug** droplet's page on DigitalOcean.
2. Click **Console** at the top right. A black window opens, already signed in.
3. If the window shows `Snug is installed but not set up. Run: sudo snug setup`, the server is ready. If it does not, the server is still installing: close the window, wait five minutes, and open it again.

To paste into the console, use the menu that appears when you right-click inside it, or press Ctrl+Shift+V.

With an SSH key, you can also connect from Terminal instead: type `ssh root@` followed by the server's ipv4 address, such as `ssh root@203.0.113.25`, and press Return. The first time, type `yes` when it asks whether to continue connecting, then your key's passphrase.

## 7. Set Snug up

In the console, type this, and press Return:

```bash
sudo snug setup
```

Snug asks one question at a time. Type each answer and press Return.

| Snug asks | You type |
|---|---|
| `The domain investors will use, like room.example.com` | your Snug address, such as `room.yourcompany.com`, without `https://` |
| `Your company's name, as investors know it` | your company's name, such as `Fernlea Robotics` |
| `Spaces region [lon1]` | nothing: press Return |
| `Bucket name` | the bucket's name, from your password manager |
| `Access key` | the Access Key ID, from your password manager |
| `Secret key` | the Secret Key, from your password manager |

Nothing appears as you type or paste the secret key. Press Return once you have pasted it.

If Snug says your domain `points to nowhere yet`, the DNS records from step 5 have not arrived yet. Carry on: Snug keeps trying.

Snug then shows its backup settings between two lines of dashes. **Copy everything between the lines into the Snug backups item in your password manager.** Without it, nobody can read the backups, including you. Then type `saved` and press Return.

Snug checks its settings and starts. The first time, this takes a few minutes.

## 8. Create your admin account

Snug asks for the first admin:

| Snug asks | You type |
|---|---|
| `Email address` | your email address |
| `Password` | a new password, at least 12 characters, saved in your password manager as **Snug admin** |
| `Password again` | the same password |

Nothing appears as you type the password.

Snug then shows a QR code. Open your authenticator app, add an account, and scan the code. The app shows a six-digit number that changes every 30 seconds.

| Snug asks | You type |
|---|---|
| `Six-digit code from the app` | the number your app shows now |

Snug shows ten recovery codes. **Copy them into the Snug admin item in your password manager.** Each one lets you sign in once if you lose your phone.

When Snug says `Snug is running`, setup is finished. You can close the console.

## 9. Sign in

1. In your browser, go to your Snug address followed by `/admin/`, such as `https://room.yourcompany.com/admin/`.
2. Type your email address and password, then the six-digit code from your app.

If the browser says the site cannot be reached, or that the connection is not private, the DNS records have not arrived yet. Wait ten minutes and try again.

## 10. Set up email

Snug emails each investor their link and the codes that prove they own their inbox, so set up email before you share anything. Follow one of these:

- [Email through Google Workspace](email-google.md), if your company's email is Gmail on your own domain;
- [Email through another provider](email.md), for anything else.

## 11. Check everything works

Click **Overview** at the top of the admin pages. The line at the top says **Everything is working**, or what needs fixing.

[Using Snug](using-snug.md) explains how to upload documents and share them.

## If something goes wrong

| What you see | What to do |
|---|---|
| The console says `snug: command not found` | The server is still installing. Close the console, wait five minutes and open it again. |
| Snug says a value `contains ' (a single quote)` | Choose a value without a `'` and run `sudo snug setup` again. |
| Snug says `A domain is needed.` | Run `sudo snug setup` again and type your Snug address at the first question. |
| The Overview shows a backup problem | Check the bucket's name and key in your password manager against step 1 and step 2, then run `sudo snug setup` again in the console. |
| Anything else | In the console, type `sudo snug doctor` and press Return. It lists what is wrong and how to fix it. |

To change your Snug address or company name later, open the console and run `sudo snug setup` again. It keeps your documents and accounts.

[The deployment reference](deployment.md) describes how the server is set up, for anyone who wants the detail.
