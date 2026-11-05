# Admin accounts

Admins are the people at your company who upload documents, issue links and read the analytics. Every admin has the same rights. Each one signs in with an email address, a password and a six-digit code from an authenticator app.

## Creating the first admin

The first admin is created on the server, from the command line:

```bash
snug createadmin --email you@yourcompany.com
```

Snug then:

1. asks for a password twice. It must be at least 12 characters, and not a common password;
2. shows a QR code. Scan it with an authenticator app, such as 1Password, Google Authenticator or Authy. If you can't scan it, type in the key printed beneath it;
3. asks for the six-digit code the app now shows. Nothing is saved until a working code is entered;
4. prints ten recovery codes.

**Store the recovery codes somewhere safe**, such as a password manager, away from your phone. Each one works once, in place of a code from the app. They are not shown again.

Further admins will be invited from the admin interface, once email is set up.

## Signing in

Go to `/admin/` on your Snug domain. Enter your email address and password, then the six-digit code from your app. The code must follow within five minutes of the password.

- **Each code works once.** If you sign out and straight back in, wait for the app to show a new code.
- **Sessions end** after an hour without activity, and after twelve hours whatever happens.
- **Too many attempts pause signing in for 15 minutes.** The limits are:
  - 20 password attempts from one address;
  - 30 wrong passwords against one account, from anywhere;
  - 10 wrong codes against one account;
  - 5 wrong codes after one correct password, which then takes you back to the password.

  The account limit is deliberately generous, so that someone typing wrong passwords cannot easily lock you out.
- **Changing your password, or resetting your second factor, signs you out everywhere.**

## Losing your phone

- If you still have your recovery codes, sign in with one of them, then set up the authenticator again.
- If you have lost the recovery codes too, another admin can reset your second factor from the admin interface.
- If you are the only admin, run this on the server:

```bash
snug reset-2fa you@yourcompany.com
```

It asks you to confirm, shows a new QR code, and issues new recovery codes. The old authenticator and the old recovery codes stop working at once.

## What is recorded

Creating an admin and resetting a second factor are both written to the audit log, along with every sign-in. Passwords, authenticator keys and recovery codes are never written to the log.

## How the secrets are kept

- **Passwords** are hashed with argon2id, using Django's defaults of 100 MiB of memory and two passes.
- **Recovery codes** are random, 80 bits each, and stored only as SHA-256 hashes.
- **Authenticator keys** have to be readable by Snug to check codes, so they are stored as they are. They live in the database on your server, and your backups are encrypted with a key only you hold.
