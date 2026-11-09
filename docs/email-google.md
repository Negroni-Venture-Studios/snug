# Sending through Google Workspace

If your company uses Google Workspace, Snug can send its emails as a mailbox on your domain, such as `rooms@example.com`. The emails then leave through Google, signed for your domain, and appear in that mailbox's *Sent* folder.

Snug sends through the Gmail API, over HTTPS. It cannot use Google's mail servers directly from DigitalOcean, which blocks the only ports they accept.

Google needs to know about Snug first, so there is a one-time setup in the Google Cloud console. It takes about ten minutes, and Google offers no way to do it from the command line. You need:

- a Google Workspace account on your domain, allowed to create Google Cloud projects (most are);
- the mailbox Snug will send from, which must be a real user rather than a group: a dedicated one, such as `rooms@`, or your own;
- Snug running, and signed in, so you can copy its redirect address from Settings.

## Step 1: create a project

1. Open [console.cloud.google.com/projectcreate](https://console.cloud.google.com/projectcreate), signed in with your Workspace account.
2. Name it **Snug**, and leave the organisation as your domain.
3. Choose **Create**, and wait until the notification says it is ready. Make sure **Snug** is the project shown at the top of the page from here on.

## Step 2: turn on the Gmail API

1. Open [console.cloud.google.com/apis/library/gmail.googleapis.com](https://console.cloud.google.com/apis/library/gmail.googleapis.com).
2. Choose **Enable**.

## Step 3: describe Snug to your users

This is the page Google shows when the mailbox approves Snug.

1. Open [console.cloud.google.com/auth/overview](https://console.cloud.google.com/auth/overview) and choose **Get started**.
2. Enter **Snug** as the app name and your own address as the user support email, then choose **Next**.
3. Under **Audience**, choose **Internal**, then **Next**. Only people on your Workspace domain can then approve Snug, and Google does not need to review it.
4. Enter your own address as the contact, choose **Next**, agree to Google's policy, and choose **Create**.

## Step 4: create the client

1. Open [console.cloud.google.com/auth/clients](https://console.cloud.google.com/auth/clients) and choose **Create client**.
2. Choose **Web application** as the application type, and name it **Snug**.
3. Under **Authorised redirect URIs**, choose **Add URI** and paste the address Snug shows in Settings, under Email, after choosing Google Workspace. On a server it looks like `https://room.example.com/admin/settings/email/google/callback`.
4. Choose **Create**.
5. Copy the **Client ID** and the **Client secret** now. Google shows the secret only once; if you lose it, add a new secret to the client.

## Step 5: connect Snug

1. In Snug, open **Settings**, and under **Email** choose **Google Workspace**.
2. Paste the client ID and the client secret, enter a code from your authenticator app, and choose **Save**.
3. Choose **Connect Google**, sign in to Google as the mailbox Snug will send from, and approve. Snug asks only to send email, and cannot read the mailbox.
4. Back in Snug, enter a fresh code from your authenticator app to finish. Every admin is emailed about the change.
5. Choose **Send a test**, and check that the email arrived.

## Sending from another address

The emails come from the mailbox that approved Snug. To send from another of its addresses, put it in **From address**, under Google Workspace in Settings, and save with a code.

That address must be one the mailbox can send as. An address on a **user alias domain**, such as `rooms@example.co.uk` when `example.com` is the primary domain, belongs to the primary account, `rooms@example.com`, and Google names that account when it approves Snug. To send as the alias, add it in that mailbox's Gmail settings, under **Accounts**, **Send mail as**, then put it in Snug's From address. Without it, Gmail quietly sends from the mailbox's own address.

## Changing or stopping it

- To send from another mailbox, choose **Disconnect**, then **Connect Google** again and sign in as the other mailbox.
- **Disconnect** also withdraws Snug's permission at Google. A Workspace admin can withdraw it too, from the mailbox's security settings in the Admin console.
- If Google stops accepting Snug's permission, the Overview says an email could not be sent: connect again.

## When it goes wrong

| What you see | Likely cause |
|---|---|
| Google says **redirect_uri_mismatch** | The redirect address in the client does not match the one Snug shows exactly, including `https://` and the domain. Copy it again. |
| Google says **Access blocked** or **org_internal** | You signed in with an account outside your Workspace domain. Sign in as a mailbox on your domain. |
| Google says the app is **blocked by your admin** | Your Workspace admin restricts apps. In the Admin console, under Security, *API controls*, allow internal apps, or trust the Snug client. |
| Snug says Google did not give **a lasting permission** | Remove Snug from the mailbox's [third-party connections](https://myaccount.google.com/connections), then connect again. |
| Snug says **invalid_client** | The client ID or secret is wrong, or the client was deleted. Paste them again. |
