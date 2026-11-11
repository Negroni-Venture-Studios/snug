# Trying Snug on your own computer

You can run Snug on your own Mac to see how it works before you set up a server. It runs only for you, at an address only your computer can open. Investors cannot reach it.

The steps are for a Mac. On Linux, install [Docker Engine](https://docs.docker.com/engine/install/) and type the same commands. Windows is not covered yet.

## 1. Install Docker Desktop

Docker Desktop runs Snug in a box of its own. It is free for a company with fewer than 250 staff and less than $10 million a year in revenue.

1. Go to [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/) and click **Download Docker Desktop**. Choose **Mac with Apple chip** or **Mac with Intel chip**: **About This Mac**, in the Apple menu, says which yours has.
2. Open the downloaded file, and drag **Docker** into **Applications**.
3. Open **Docker** from Applications. Accept the terms, and choose **Skip** when it asks you to sign in or answer a survey.
4. Wait until the bottom left of the Docker window says **Engine running**.

Docker must be open whenever you use Snug.

## 2. Open Terminal

Press ⌘ Space, type `Terminal`, and press Return. A window opens where you type commands.

Type each command below, or copy and paste it, and press Return.

## 3. Make a folder and a key

Make a folder for Snug's settings, and go into it:

```bash
mkdir -p ~/snug && cd ~/snug
```

Make the secret key Snug needs. The first time, Docker downloads Snug, which takes a minute or two:

```bash
docker run --rm ghcr.io/negroni-venture-studios/snug:edge python -c 'import secrets; print("SNUG_SECRET_KEY=" + secrets.token_urlsafe(64))' > snug.env
```

Keep the file this makes, `snug.env`, in the `snug` folder in your home folder. If it is lost, you will have to sign in again.

## 4. Start Snug

```bash
docker run --detach --name snug --publish 127.0.0.1:8000:8000 \
  --env-file snug.env --env SNUG_DOMAIN=localhost:8000 --env SNUG_HTTPS=false \
  --volume snug-data:/var/lib/snug \
  ghcr.io/negroni-venture-studios/snug:edge
```

Docker prints a long string of letters and numbers. That means Snug has started.

## 5. Create your admin account

```bash
docker exec --interactive --tty snug snug createadmin --email you@example.com
```

Put your own email address in place of `you@example.com`. Snug asks:

| Snug asks | You type |
|---|---|
| `Password` | a new password, at least 12 characters |
| `Password again` | the same password |

Nothing appears as you type the password.

Snug then shows a QR code. Open your authenticator app, such as 1Password, Google Authenticator or Authy, add an account, and scan the code. If the code does not fit in the window, make the window bigger, or type the key shown under it into the app instead.

| Snug asks | You type |
|---|---|
| `Six-digit code from the app` | the number your app shows now |

Snug shows ten recovery codes. Each lets you sign in once without your phone. Keep them somewhere safe.

## 6. Sign in

Open [http://localhost:8000/admin/](http://localhost:8000/admin/) in your browser, and sign in with your email address, your password and a code from your app.

The Overview says that nothing has been backed up. That is expected: backups need a server.

## Reading Snug's emails

On your own computer, Snug does not send email. It saves each one instead, such as the code an investor would get. To read them:

```bash
docker exec snug sh -c 'cat /var/lib/snug/outbox/*'
```

## Stopping and starting

To stop Snug:

```bash
docker stop snug
```

To start it again later, open Docker, then:

```bash
docker start snug
```

Your documents and accounts are kept in between.

## Updating to the latest version

```bash
cd ~/snug
docker pull ghcr.io/negroni-venture-studios/snug:edge
docker rm --force snug
```

Then run the command from step 4 again. Your documents and accounts are kept.

## A forgotten password

```bash
docker exec --interactive --tty snug snug reset-password you@example.com
```

It asks for the new password twice. Your authenticator app keeps working as before.

## Removing it all

This deletes Snug and everything uploaded to it:

```bash
docker rm --force snug && docker volume rm snug-data && rm -r ~/snug
```

When you are ready to share documents with investors, [put Snug on a server](deployment.md).
