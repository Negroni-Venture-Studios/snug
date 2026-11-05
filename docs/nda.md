# The NDA

Snug can ask a viewer to accept a non-disclosure agreement before they see anything a link shares.

## What it is, and what it is not

**An NDA acceptance in Snug is a record of acceptance, not an electronic signature.** It shows that someone with access to the link's email address, giving the name they typed, ticked a box to accept a specific text, at a specific time, from a specific IP address and browser. Whether that is enough for your purposes is a question for your lawyer, not for Snug. If you need a signature, use a signing service, and share the room once it is signed.

## Setting it up

1. In **Settings**, under **NDA**, paste your agreement and choose **Save agreement**. This is version 1.
2. To ask for it on a room, open the room and tick **Ask for the NDA before anything is shown**. Every link to that room then asks for it, including links already sent.
3. To ask for it on a link to a single document, tick **Ask for the NDA first** when you create the link.

There is one agreement for the whole instance. Most companies have one NDA; if yours differs between rooms, say so in an issue.

## Changing it

Saving changed text makes a new version. Everyone asked for the NDA accepts the new version on their next visit, so every record matches the exact words that person agreed to. Earlier acceptances stay on record.

## What is recorded

For each acceptance, Snug keeps:

- the name they typed, and the email address their link was sent to;
- the link, and the version of the agreement;
- the time, their IP address and their browser's user agent;
- a SHA-256 fingerprint of the exact text accepted.

Acceptances are never changed or deleted by Snug. Each one is also written to the audit log as `viewer.nda_accepted`. Acceptance is per link: someone with two links to the same room accepts on each.
