# Sending through Google Workspace

If your company uses Google Workspace, Snug can send its emails as a mailbox on your domain, such as `rooms@example.com`. The emails leave through Google, signed for your domain, and appear in that mailbox's *Sent* folder.

Snug sends through the Gmail API, over HTTPS. It cannot use Google's mail servers directly from DigitalOcean, which blocks the only ports they accept.

Google needs to know about Snug first, so there is a one-time setup in the Google Cloud console. Google offers no way to do it from the command line. This guide assumes you have never used Google Cloud before, and takes you through every screen. It takes about fifteen minutes.

## What you need

- **A Google Workspace account that is an administrator**, or one allowed to create Google Cloud projects (most are).
- **The mailbox Snug will send from.** It must be a real user with a Gmail licence, not a group. A dedicated user, such as `rooms@`, is best: investor emails then stay out of your own *Sent* folder, and sending does not stop if you leave or change your password. Your own mailbox works too, and is the quickest way to try it.
- **Snug running, and you signed in.** You will copy its redirect address from Settings, and paste the client into it at the end.
- **Your domain's email records in place.** Mail from a domain without SPF, DKIM and DMARC goes to spam. If you are not sure yours has them, see [Your domain's email records](#your-domains-email-records) first.

## Before you start: one Google account at a time

If you are signed in to more than one Google account in your browser, Google Cloud may open in the wrong one, and with it the wrong organisation. Look at the address bar: `authuser=1` means the second account you signed in to.

Use a browser profile, or a private window, signed in only to your Workspace account. Or, once Google Cloud is open in the right account, move between its pages with its menus and the links in its left-hand sidebar, rather than pasting addresses. Those keep you in the same account and project.

## Step 1: create a project

A project holds everything you set up for Snug, kept apart from anything else in your Google Cloud account.

1. Open [console.cloud.google.com](https://console.cloud.google.com), signed in with your Workspace account. Accept the terms of service if Google asks.
2. A banner offers a free trial with credit. Choose **Dismiss**. Snug needs no billing, and the trial can be started only once, with its credit expiring 90 days later, so keep it for when you need it.
3. Google may have made a project called *My First Project*. Leave it; it costs nothing. Click its name in the bar at the top, next to the Google Cloud logo. A box called *Select a resource* opens.
4. Choose **New project**, at its top right.
5. Name it **Snug**. Leave **Organisation** as it is. It is your Workspace organisation, named after your primary domain, which may not be the domain you send from. Leave **Location** as it is too. Google gives the project an ID, such as `snug-514212`, which you never need.
6. Choose **Create**. After a few seconds a notification appears under the bell icon.
7. Click the project name at the top again and choose **Snug**. The welcome page should now say *You're working in* your organisation, then **Snug**.

From here on, check that the bar at the top says **Snug** before each step. Google Cloud works in whichever project it shows.

## Step 2: turn on the Gmail API

1. In the search box at the top, type **Gmail API** and choose it from the results. Or open [console.cloud.google.com/apis/library/gmail.googleapis.com](https://console.cloud.google.com/apis/library/gmail.googleapis.com) if you are signed in to one account only.
2. Check the bar at the top says **Snug**, and choose **Enable**.

The page changes to *API/Service details*, with **Status: Enabled**. Ignore its **Create credentials** button; step 4 makes the client a more direct way.

## Step 3: describe Snug to your users

This is the page Google shows when the mailbox approves Snug.

1. In the left-hand sidebar, choose **OAuth consent screen**. Google opens *Google Auth Platform*, saying it is not configured yet. Choose **Get started**.
2. **App information.** Enter **Snug** as the app name, choose your own address as the user support email, and choose **Next**.
3. **Audience.** Choose **Internal**, then **Next**. Only accounts in your Workspace organisation can then approve Snug, and Google does not need to review it, so this choice matters.
4. **Contact information.** Enter your own address, and choose **Next**.
5. **Finish.** Tick the box agreeing to the *Google API Services: User Data Policy*, choose **Continue**, then **Create**.

A message says *OAuth configuration created*, and the page shows *OAuth overview*.

## Step 4: create the client

The client is how Snug identifies itself to Google. It has an ID, which is not secret, and a secret, which is.

1. On *OAuth overview*, choose **Create OAuth client**. Later, you can find it under **Clients** in the sidebar.
2. **Application type:** choose **Web application**.
3. **Name:** **Snug**, which only you see.
4. **Authorised JavaScript origins:** leave empty. Snug talks to Google from the server, not the browser.
5. **Authorised redirect URIs:** choose **Add URI**, and paste the address Snug shows in Settings, under Email, in step 1 after you choose Google Workspace. On a server it looks like this:

        https://room.example.com/admin/settings/email/google/callback

   It must match exactly, with `https://`, your domain, and no slash at the end. If you also try Snug on your own computer, add its address as a second URI, such as `http://localhost:8000/admin/settings/email/google/callback`. Google allows plain `http` only for `localhost`.
6. Choose **Create**.

A box shows the **Client ID** and the **Client secret**. **Copy the secret before you close the box.** Google shows it only once. Save both in your password manager, as one entry. The box also offers **Download JSON**. If you use it, delete the file once the values are saved, since it holds the secret in plain text.

If you lose the secret, open the client under **Clients**, choose **Add secret**, and delete the old one afterwards.

Google says changes can take a few minutes to take effect. If Google later says *redirect_uri_mismatch*, wait five minutes before looking for a mistake.

## Step 5: connect Snug

In Snug, open **Settings**, and under **Email** choose **Google Workspace**. Snug lays the rest out in three numbered steps, and each moves you on to the next.

1. **Your Google client.** Paste the client ID and the client secret. Leave **From address** empty for now. Enter a code from your authenticator app, and choose **Save**. Afterwards the secret shows as dots: it is saved, and never shown again.
2. **The sending mailbox.** Choose **Connect Google**. Google asks which account to use; choose the mailbox Snug will send from. Google then lists what Snug asks for, which should be only *Send email on your behalf*. Snug cannot read the mailbox. Choose **Continue**.

   Back in Snug, a green notice names the mailbox Google approved. Enter a fresh code from your authenticator app to finish. Connecting changes where Snug's email goes, so it needs its own code, even after the one in step 1. Every admin is emailed about the change.
3. **Send a test.** Snug emails you, through the mailbox. Check that it arrived, and not in spam.

Google may name a different address from the one you expected. If your Workspace has more than one domain, it names the mailbox's **primary** address, as the next section explains.

## Sending from another address

Snug sends as the mailbox that approved it. To send as another of its addresses, put it in **From address**, in step 1, and save with a code.

That address must be one the mailbox can send as. Workspace domains come in two kinds.

- **A secondary domain** has users of its own. To send as `rooms@second.example`, create that user and connect it instead.
- **A user alias domain** gives every user a second address on it, such as `alex@example.co.uk` for `alex@example.com`. The alias belongs to the primary account, and that is the account Google names when it approves Snug. To send as the alias, open that mailbox's Gmail, then **Settings**, **Accounts**, **Send mail as**, and add it. Then put it in Snug's **From address**.

Without *Send mail as*, Gmail quietly sends from the mailbox's primary address instead.

## Your domain's email records

Receiving servers check three DNS records before they trust mail from your domain. Without them, Snug's emails go to spam or are refused. They live in the DNS of the domain the emails come **from**, and every domain you send from needs its own set, alias domains included.

Your Workspace administrator may have set them up already. To check, open the [Admin console](https://admin.google.com), then **Account**, **Domains**, **Manage domains**, and look at **Email setup status** for each domain. It lists MX, SPF and DKIM, each marked *Complete* or *Incorrect*. Google says changes can take up to 72 hours to show there, though most show within hours.

You add the records at your DNS host: your registrar, Cloudflare, or wherever your domain's records are kept.

### SPF

SPF lists the servers allowed to send mail for your domain. For Workspace it is a `TXT` record on the domain itself, which DNS hosts often write as `@`:

    v=spf1 include:_spf.google.com ~all

**A domain can have only one SPF record.** If yours already has one, for another service, add `include:_spf.google.com` to it rather than adding a second record. Two SPF records make both invalid.

### DKIM

DKIM signs each message with a key that only Google holds, and publishes the matching public key in your DNS. It is the record that matters most, because it proves to other mail servers that the message really came from your domain. Without it, Google signs with a key of its own, which does not count for your domain.

1. In the [Admin console](https://admin.google.com), open **Apps**, **Google Workspace**, **Gmail**, then **Authenticate email**.
2. Under **Selected domain**, choose the domain. Check it carefully if you have domains with similar names. Each needs its own key, and a key published on the wrong domain fails.
3. Choose **Generate new record**. Choose a **2048** bit key if your DNS host accepts it, and leave the prefix as `google`.
4. Google shows a **DNS host name**, `google._domainkey`, and a **TXT record value**, starting `v=DKIM1; k=rsa; p=`. At your DNS host, add a `TXT` record with that name and value. The name is `google._domainkey`, never `@`. Many hosts add your domain to the end of the name for you, so type only `google._domainkey`.
5. Wait until the record is published (see [Checking the records](#checking-the-records)), then come back and choose **Start authentication**. The status changes to *Authenticating email with DKIM*.

If **Start authentication** fails, the record Google finds does not match the key it generated. Check the name, the domain, and that the value was pasted whole.

### DMARC

DMARC tells receiving servers what to do with mail that claims to be from your domain but fails SPF and DKIM, and where to send reports. It is a `TXT` record named `_dmarc`. If you have none, start with this, using an address of yours that can receive the reports:

    v=DMARC1; p=none; rua=mailto:dmarc@example.com

`p=none` asks for reports without affecting delivery. Once the reports show that your real mail passes, usually after a couple of weeks, change it to `p=quarantine`. Gmail and Yahoo expect every bulk sender to have a DMARC record.

If you already have a DMARC record, leave it as it is.

### Checking the records

Once a record is added, ask for it from a terminal:

    dig +short TXT example.com
    dig +short TXT google._domainkey.example.com
    dig +short TXT _dmarc.example.com

Each should print the value you added. A new record usually appears within minutes. A changed one can take as long as its old *TTL*, often an hour.

Then send a test from Snug, open it in Gmail, and choose **Show original** from the message's menu. The top of the page should say **PASS** for SPF, DKIM and DMARC.

### When your DNS host is awkward

DNS hosts differ, and a few things catch people out.

- **Long values.** A 2048 bit DKIM key is longer than a single DNS text string can hold, so it has to be split into pieces. Most hosts do this for you. Some refuse the value, or fail with an unhelpful error. If yours does, generate a **1024** bit key instead, which fits in one piece and is accepted everywhere.
- **Quotation marks.** Some hosts want the value in quotation marks, and some add their own, which doubles them. Paste the value as Google shows it, then check what is published with `dig`.
- **An error after saving.** If the DNS host's page shows an error after you save, reload it before trying again. The record may have saved, or the page may have stopped working until it is reloaded.
- **Leftover records.** Setting up Workspace sometimes needs a temporary record to prove you own the domain, such as an extra `MX` record with a long random name. Once the domain is verified, remove it. An extra `MX` record can make **Email setup status** show *Incorrect* even though mail arrives. If mail already reaches your Gmail, you do not need **Activate Gmail** there.

## Changing or stopping it

- To send from another mailbox, choose **Disconnect** in step 2, then **Connect Google** again and approve as the other mailbox.
- **Disconnect** also withdraws Snug's permission at Google. A Workspace admin can withdraw it too, from the mailbox's security settings in the Admin console.
- If Google stops accepting Snug's permission, the Overview says an email could not be sent. Connect again.
- To stop Snug sending through Google altogether, choose **Clear email settings**, under the sentence at the top of Email that says how Snug sends, and enter a code. Snug forgets the client and the mailbox, and withdraws its permission at Google, after telling every admin. Email then uses the SMTP settings in `snug.env`, if there are any, or is off until it is set up again.

## When it goes wrong

| What you see | Likely cause |
|---|---|
| Google Cloud shows the wrong organisation, or your project is missing | You are signed in to more than one Google account, and the page opened in another. See [Before you start](#before-you-start-one-google-account-at-a-time). |
| Google says **redirect_uri_mismatch** | The redirect address in the client does not match the one Snug shows exactly, including `https://` and the domain. Copy it again, then wait five minutes. |
| Google says **Access blocked** or **org_internal** | You signed in with an account outside your Workspace organisation. Sign in as a mailbox in it. |
| Google says the app is **blocked by your admin** | Your Workspace admin restricts apps. In the Admin console, under **Security**, **Access and data control**, **API controls**, allow internal apps, or trust the Snug client. |
| Google names a different address from the one you expected | It is the mailbox's primary address, on another of your domains. See [Sending from another address](#sending-from-another-address). |
| Snug says Google did not give **a lasting permission** | Remove Snug from the mailbox's [third-party connections](https://myaccount.google.com/connections), then connect again. |
| Snug says **invalid_client** | The client ID or secret is wrong, or the client was deleted. Paste them again. |
| The test arrives, but in spam | SPF, DKIM or DMARC is missing or failing. See [Your domain's email records](#your-domains-email-records), and check **Show original**. |
| The test comes from the wrong address | The From address is not under the mailbox's **Send mail as** in Gmail. |
