# Snug: design

This file records what we are actually building and why. The original briefing is kept in [docs/BRIEF.md](docs/BRIEF.md) for the record. The brief was advisory: where this file and the brief disagree, this file wins.

Status: **draft, under discussion** (5 November 2026). Sections marked *proposed* have not yet been agreed.

## 1. Decisions

Agreed on 5 November 2026 unless noted.

| Area | Decision | Why |
|---|---|---|
| Web framework | Django 6.1, moving to 6.2 LTS when it ships (April 2027, supported to April 2030) | CSRF, server-side sessions, versioned migrations, argon2id, CSP and security headers are built in and long audited. Hand-written security primitives are where Papermark's CVEs live. Fewer dependencies than the Starlette path. |
| Django admin | `django.contrib.admin` is not installed | Extra attack surface, and it would not enforce TOTP. |
| Audit log | A minimal append-only audit table exists from step 1; viewing and CSV export arrive in step 6 | Admin logins must be audited from the first login. |
| Lost second factor | One-time recovery codes issued at enrolment; `snug reset-2fa` on the server; any admin can reset another admin's second factor | The brief left this unspecified. |
| Removing admins | An admin cannot delete themselves, and the last admin cannot be deleted | Avoids locking a company out of its own instance. |
| Second factor | TOTP with a one-step window either side and replay protection (the last used step is stored). Codes must be ASCII digits. Ten recovery codes of 80 bits each, stored as SHA-256 hashes. TOTP keys stored unencrypted. | The window covers clock drift; replay protection stops a shoulder-surfed code being used twice. TOTP keys must be readable to check codes; the database stays on the founder's server, and backups are encrypted. |
| Sign-in limits | Per address: 20 password, 20 code and 10 recovery attempts. Per account: 30 password failures, 10 code failures and 5 recovery failures. All in 15-minute windows, held in SQLite so every process shares them. 5 wrong codes after one correct password returns to the password step. | The account limit is generous on purpose: a low one lets anyone lock a founder out by typing wrong passwords, and a correct password only reaches the code step. |
| Admin sessions | A new session id at each step. Five minutes from password to code. Ending after one hour idle or twelve hours absolute. The session hash covers the password and the TOTP key, so changing either signs out everywhere. | Fixation-proof, short-lived, and revocable without scanning the session table. |
| PDF handling | Uploads stream to a temporary file in the data folder, with a size limit, a hash, and a `%PDF-` header check, then an inspection in the worker, before a rename into `originals/<random>.pdf`. The uploaded name is kept for display only. | The brief's file-handling rule: generated names, and paths from records only. |
| Render worker | `python -I -m snug.rendering.worker`, with a clean environment (no `SNUG_*`), given the PDF as an open file descriptor, never a path, and returning results through a pipe, which Snug reads in bounded chunks: past 64 MB or the deadline, the worker's whole process group is killed. It limits itself to 1 GiB of memory (enforced on Linux), 60 s CPU, 64 MB output and 32 open files, then confines itself with Linux Landlock: no filesystem access except reading system fonts, and no network (Landlock ABI 4, Linux 6.7, or later). Timeouts are 60 s to inspect and 30 s to render. At most 2,000 pages; renders capped at 6.4 megapixels and 8,000 px a side. Snug's own processes (gunicorn and the `snug` command) mark themselves non-dumpable, so a compromised worker cannot read their `/proc` entries or environment. **Still to do (step 7):** run the worker as a separate, unprivileged user in the container, and have `snug doctor` report the confinement level. | A malformed or hostile PDF cannot take the server down. Even if it took over PDFium, it could not read the secret key, the database or other documents. Raised by an automated security review of step 2a. |
| Render cache | Plain pages per version, page and width (800 or 1,600 px), as WebP, in `renders/`. Watermarks are stamped per request in step 4. | One cached page serves every viewer, and the cache can be deleted and rebuilt. |
| Audit log storage | Append-only, refused by the model, its queryset and SQLite triggers | Not even raw SQL from inside Snug can rewrite history. |
| Admin invites | Moved to step 3, when email exists | Invites need email. |
| Rate limiting | A small database-backed limiter of our own, not a library | Shared correctly across server processes; easy to test. |
| Client IP | `X-Forwarded-For` is trusted only from Caddy | Rate limits, UK-only mode and the audit log all depend on the real address. |
| Verification codes | Six digits, single use, short expiry, at most five wrong attempts per code, rate limited per link, per IP and per email address | The brief's limits alone allowed unlimited guesses per code. |
| Viewer session lifetime | 7 days by default, configurable | Revocation is checked on every request, so a long session does not weaken it. |
| Render cache | Cache the plain render per document version and page; stamp the watermark on every request | The watermark carries the date, so a per-link cache would go stale daily, and it avoids one copy of every deck per investor. |
| NDA | Available on single-document links as well as rooms | A document shared on its own may need an NDA too. |
| Cover message | Optional plain-text message shown before the first page | Cheap, and founders asked for it. |
| Weekly digest | Not in version 1 | Needs a scheduler and more email design; first-open notifications cover the urgent case. |
| Backups | Litestream for SQLite; an rclone sidecar container for uploaded originals; both encrypted with a key only the founder holds | Litestream only replicates SQLite. Negroni never holds data, and the bucket provider should not be able to read it either. |
| GeoIP | DB-IP IP to Country Lite (CC BY 4.0, checked 5 November 2026), downloaded at deploy time and refreshed monthly; attribution in the docs and the admin interface | Bundling it in the image would go stale. The monthly fetch is documented; it is not telemetry. |
| `snug doctor` email check | Sends a code and asks the operator to type it back | Snug cannot read the operator's inbox. |
| Periodic work | A small background loop inside `snug serve` (code expiry, cache clean-up) | The container has no cron. |
| Coverage | A floor enforced in CI; no Codecov for now | One fewer third-party service and token. |
| CI actions | Every third-party action pinned to a commit SHA, kept current by Dependabot | Supply-chain hardening. |
| Changelog | A plain `CHANGELOG.md` with an `[Unreleased]` section | Vera's `changelog.d/` fragments solve a throughput problem we do not have. |
| Security headers | Strict CSP from `default-src 'none'`; `Referrer-Policy: same-origin`; `__Host-` cookies; HSTS for one year, without subdomains or preload; Permissions-Policy and Cross-Origin-Resource-Policy | Viewer URLs carry tokens, so no Referer may reach another site. Not `no-referrer`: browsers then send `Origin: null` on form posts, which fails CSRF. Subdomains and preload are the founder's choices, not Snug's. |
| Logging | No request access log, in Snug or in gunicorn | Viewer URLs carry tokens. |
| Health check | `/healthz` is answered before the host check | Docker and Caddy probe the container by its internal name. |
| Commit trailer | `Co-Authored-By: Claude <noreply@anthropic.invalid>` | As Vera: the `.com` address resolves to an unrelated GitHub account. |
| Branding | The Negroni brand kit is inspiration and palette, not a rulebook. No commercial brand fonts; Geist and Geist Mono (OFL) instead. | PP Hatton and NB Grotesk Pro Mono need commercial licences and cannot ship in an MIT repository or a public image. |
| Front end | Revision 2 (section 3): Negroni palette, Geist type, Phosphor icons, the reading heatmap; its stylesheet seeds `base.css` | Revision 1 was calm but bland. |
| Logo | The half-glazed snug door (section 3.14) | Door, monogram and Negroni sparkle; a snug is a pub's private room. |
| Trademarks | Logo files are MIT with the code; `TRADEMARKS.md` asks forks shipped as separate products to rename | Keeps "Snug" meaning the Negroni project without heavy policy. |
| Theme | Dark by default; a dark, light and auto toggle in every header | Agreed 5 November 2026. |

## 2. Dependencies

| Package | Licence | Purpose | From step |
|---|---|---|---|
| Django (+ asgiref, sqlparse) | BSD-3-Clause | Framework | 1 |
| argon2-cffi (+ argon2-cffi-bindings, cffi, pycparser) | MIT (cffi MIT-0, pycparser BSD-3-Clause) | argon2id | 1 |
| pyotp | MIT | TOTP | 1 |
| segno | BSD-3-Clause | QR codes for TOTP enrolment, as SVG | 1 |
| gunicorn | MIT | WSGI server | 1 |
| whitenoise | MIT | Serves static files from the app container | 1 |
| pypdfium2 | BSD-3-Clause / Apache-2.0; bundled PDFium carries FreeType, ICU, libjpeg-turbo, lcms, OpenJPEG, libpng, zlib and abseil notices | PDF rendering | 2 |
| Pillow | MIT-CMU | Images and watermarks | 2 |
| maxminddb, or a CSV import into SQLite | Apache-2.0 | Reading the GeoIP database | 8 |
| Geist and Geist Mono fonts (vendored, v1.8.2) | SIL Open Font Licence 1.1 | Interface type | 1 |
| Phosphor icons (vendored SVG, core v2.0.8) | MIT | Interface icons | 1 |

Development: pytest, pytest-django, pytest-cov, ruff, mypy, django-stubs, pre-commit, pip-audit, pip-licenses. CI only: cyclonedx-bom, gitleaks, CodeQL.

Separate services, not linked into Snug: Caddy and Litestream (both Apache-2.0), rclone (MIT).

`THIRD_PARTY_LICENCES.md` ships in the container image, because the PDFium binary inside pypdfium2's wheel requires its notices to travel with it.

## 3. Front end (revision 2, agreed 5 November 2026)

Revision 2 (5 November 2026) moves away from the estate house style and towards the Negroni brand. Revision 1 was calm but, in Liz's words, bland. This revision was also reviewed against the design-taste skill. That skill is advisory, and it is written for landing pages rather than admin tools, so only its general rules were applied: type, colour discipline, copy, button and form contrast, and its list of AI tells. Revision 2 was chosen on 5 November 2026, and revision 1 has been removed. The open questions in section 3.13 still need answers.

### 3.1 Design read

A trust-first tool for founders (the admin) and investors (the viewer). The visual language is clean and confident, borrows the Negroni palette, and is built from native CSS and server-rendered HTML.

On the taste skill's dials: variance 3 (orderly, aligned, few surprises), motion 2 (hover and press feedback only), density 5 in the admin and 2 in the viewer.

### 3.2 Principles

- **Two surfaces, two owners.** The admin is Snug's own, built from the Negroni palette. The viewer belongs to the founder: their logo and accent colour on plain neutrals, with no Snug or Negroni branding.
- **Server-rendered first.** Every page works as plain HTML forms and links. JavaScript only enhances.
- **The strict CSP shapes the design.** No inline scripts, no `style=""` attributes and no third-party origins. Section 3.10 lists the consequences.
- **Accessible by default.** WCAG 2.2 AA throughout, with tests where possible.
- **Copy.**
  - British English, plain and low-drama, in sentence case.
  - Form labels sit above their fields, with errors below them.
  - No em or en dashes in interface text, and no middle dots used as separators.
  - Missing values are written in words ("No expiry"), never shown as a dash.
  - Figures come from real data, never invented precision.
  - No exclamation marks and no emoji.

### 3.3 Typography

- **Geist and Geist Mono** (SIL Open Font Licence 1.1), self-hosted as variable WOFF2 files: 70 KB and 72 KB.
  - Pinned to release v1.8.2, which was more than two weeks old when it was chosen.
  - Loaded with `font-display: swap`, and the sans is preloaded.
  - The fallback is the system stack, so the page works before the font arrives.
- **Weights echo the brand.** Negroni sets display text in light weights, so page titles and large figures use Geist Light (300) at 2.1rem. Section headings are Medium (500) at 1.1rem. Body text is Regular at 15px. Labels are Medium at 0.875rem, in sentence case rather than spaced capitals.
- **Mono for details**, as the brand uses NB Grotesk Mono for metadata. Geist Mono is reserved for identifiers, page counters, link IDs, copied links and the watermark.
- **Tabular figures** wherever numbers line up.
- **No serif.** PP Hatton cannot be licensed for an open-source project, and a substitute serif in a product UI would read as decoration.

### 3.4 Colour

Each token is written once with CSS `light-dark()`, resolved by `color-scheme` on the root element, so there are no duplicated dark-mode blocks.

**Dark is the default** on both surfaces. A round three-state toggle in every header cycles dark, light and auto (auto follows the operating system). It is the toggle from agentlanguages.dev: a moon, a sun, and a half-filled circle. Its label names the current state, and the choice is saved per browser. A small `theme-init.js` in `<head>` applies the saved choice before first paint; it is a file, not an inline script, because of the CSP. Contrast figures below are the lowest measured against any surface the token is used on.

**Admin palette** (Negroni).

| Token | Light | Dark | From | Lowest contrast (light / dark) |
|---|---|---|---|---|
| `--bg` | `#fdfbf9` | `#1A0B00` | Vermouth 500 in dark | |
| `--surface` | `#ffffff` | `#241307` | | |
| `--tint` | `#f7efe8` | `#2e1b0d` | Vermouth 100, muted | |
| `--border` | `#ece3db` | `#3a2516` | | |
| `--text` | `#1A0B00` | `#f6e8dc` | Vermouth 500 / 100 | 16.9 / 13.7 |
| `--text-2` | `#6a5141` | `#d2b9a5` | Vermouth, desaturated | 6.4 / 8.8 |
| `--text-3` | `#7a6253` | `#ad927e` | | 5.0 / 5.6 |
| `--link` | `#B24400` | `#FF8133` | Campari 500 / 200 | 5.0 / 6.6 |
| `--action` with `--on-action` | `#FF6200` with `#1A0B00` | same | Campari 300 with Vermouth 500 | 6.4 |
| `--ok` | `#127D47` | `#1ABC6B` | PCB 400 / 200 | 4.6 / 6.6 |
| `--warn` | `#8a5a00` | `#FFAB1A` | Citrus, deepened / Citrus 300 | 5.2 / 8.7 |
| `--danger` | `#a8322b` | `#f08a7e` | | 5.9 / 6.8 |

- **Primary buttons** use the brand's own call-to-action colour, Campari 300, with dark Vermouth text rather than white. White on Campari 300 measures 3.0:1 and fails AA.
- **Chrome.** A 4px Campari 300 bar over a 2px Citrus 300 bar runs across the top of every admin page, adapted from the header bars of the Negroni document template. A short PCB 300 rule sits in the footer. This chrome is where most of the brand's colour lives, so the rest of the page can stay quiet.
- **Reading intensity**, used in the admin's reading heatmap, runs Citrus 100, Citrus 300, Campari 300, Campari 500 in light mode, and from dim amber up to Campari 200 in dark mode.
- **One accent.** Campari is the only action colour. Citrus appears only in the chrome and the heatmap. PCB appears only for "all good" states.
- The taste skill warns against warm near-black text and cream backgrounds as a default "premium" look. We use them here because the Negroni brand book names these exact colours, not as a default reach.

**Viewer palette** (neutral stone, so any founder colour sits well on it).

| Token | Light | Dark |
|---|---|---|
| `--bg` | `#fafaf9` | `#1c1917` |
| `--surface` | `#ffffff` | `#262220` |
| `--text` | `#1c1917` | `#f5f5f4` |
| `--text-2` | `#57534e` | `#d6d3d1` |
| `--text-3` | `#6b6661` | `#a8a29e` |
| `--brand`, `--brand-text`, `--on-brand` | From `/brand.css`, chosen by the server to pass 4.5:1 | |

### 3.5 Shape, depth and motion

- **One radius rule.** 8px on everything rectangular (buttons, inputs, panels). 4px on small inline labels. 2px on document pages, so they read as paper.
- **No shadows except under the document page** in the reader.
- **No decorative dots.** Status labels are tinted text ("Active", "Expired", "Revoked").
- **Motion is static by design.** Hover changes a colour, and a pressed button moves down by 1px. Everything is turned off under `prefers-reduced-motion`.

### 3.6 Icons

Phosphor icons, in the Regular weight (MIT licence), vendored as SVG files and pinned to `phosphor-icons/core` v2.0.8. They are inlined through a template include and take `currentColor`, so they follow the theme. They replace the Lucide icons of revision 1, which the taste skill discourages.

### 3.7 Admin interface

Real URLs, not hash routing, because every action is a CSRF-protected form post.

**Header.** The chrome bar, then the Snug lockup (section 3.14) with the company name beside it, the admin's email address and *Sign out*. The tabs are **Overview**, **Documents**, **Rooms**, **Links**, **People**, **Audit log** and **Settings**. The active tab is underlined in Campari.

- **Overview.** A stat strip, recent activity with first opens marked "New", and a status line. The status line names any problem from the `snug doctor` checks, such as an old backup or failing email.
- **Documents.**
  - The list shows name, current version, page count, number of links and last viewed.
  - The page title is set in Geist Light, with the version, page count and upload date beneath it.
  - **Stat strip.** Viewers, visits, median reading time, and how many read every page. These are large figures separated by hairlines, with no card boxes.
  - **How it was read.** One aligned grid. The top row is a bar chart of average time per page. Below it, each viewer has a row of 18 cells, one per page, shaded by time spent; empty cells were never seen. Visits, total time and last seen sit to the right. Because the page columns line up, the founder can see where attention went and where each person stopped, in one view. Each cell carries its value as a tooltip, and the totals are in text.
  - **Links to this document** as plain rows, with *Revoke* or *Extend* where they apply.
- **Rooms.** A list, and for each room its folders and documents, the NDA text (versioned, with history), the room's links and the UK-only setting.
- **Links.** Every link, filterable: recipient, note, target, created, expiry and status. Revoking goes through a confirmation page that names the recipient.
- **People.** One row per viewer email address, with everything that person has opened across the instance, using the same page strips.
- **Audit log.** A dense, filterable table and a CSV export.
- **Settings.**
  - Branding: logo and accent colour, with a live preview and a contrast warning.
  - Email: SMTP status and a test send.
  - Admins: invite, remove, reset second factor.
  - Sessions: lifetimes.
  - Privacy: an optional link to the founder's own privacy policy.
  - System: version, backup status, GeoIP database date with the DB-IP attribution, and licences. The version number lives here, not in the footer.
- **New link.** The form is on the left. On the right is what happens next, including the link to copy.

**Phones.** One breakpoint at 860px for layout and one at 640px for spacing. On phones, the stat strip becomes two by two. Each viewer's row in the reading grid stacks: email address, then the page strip, then "3 visits, 17 min 26 s". Link rows drop their secondary columns.

**Login.** Email and password, then a six-digit code. A *Use a recovery code* link leads to a separate form. First-time enrolment shows the QR code, then the recovery codes once.

### 3.8 Viewer

The founder's logo at the top left, and nothing competing with the document. It is designed phone-first.

1. **Landing.** "**Fernlea Robotics** has shared **Seed deck** with you", then an email field and *Send code*. On wide screens, an **About this link** panel sits beside it:
   - who sent it, which document, the access given and the expiry;
   - the cover message;
   - a plain statement that the company will see when the viewer opens the document and how long they spend on each page, and that every page carries their email address.
2. **Code.** One six-digit field (`autocomplete="one-time-code"`). Errors appear below it and say what happens next: "That code is not right. You have 4 tries left."
3. **NDA**, when required. The NDA text in a readable panel, a name field, an *I accept* checkbox, and a statement of what is recorded (name, email, time, IP address). The page says plainly that this is a record of acceptance, not a signature.
4. **Room index.** Folders as plain section headings (one level only), each document with its page count and last update.
5. **Reader.** One page image at a time, with *Previous*, "5 of 18", *Next* and a page picker. Arrow keys work and pinch-zoom is never disabled. The watermark footer reads "Shared with *email*", the date, and "Link 7Q2K" in three columns, with no separators.
6. **Unavailable.** One page for unknown, expired and revoked links alike.

**Founder branding under a strict CSP.**
- The accent colour reaches the page through a generated `/brand.css`, served with a cache-busting hash. The server picks `--brand-text` and `--on-brand` so that both pass 4.5:1.
- Logos are accepted as PNG or JPEG only and re-encoded through Pillow. SVG is not accepted, because an uploaded SVG can carry script.
- An optional second logo can be uploaded for dark mode. Without one, the logo sits on a light rounded tile.

### 3.9 Email

- Plain text plus a simple HTML version, with the founder's name and accent colour.
- No remote images and no tracking pixels.
- The code email's subject is "Your code for *Fernlea Robotics*: 482913", so the code can be read from the notification. The body names the document and says to ignore the email if it was not requested.
- First-open notifications to admins say who, what, when and where (country), with a link to that person's page.

### 3.10 Front-end architecture

- **Templates.** Django templates with `{% include %}` partials. No front-end framework and no build step.
- **Stylesheets.** `base.css` holds tokens and components, with small `admin.css` and `viewer.css` layers and the generated `/brand.css`. Static files are fingerprinted by `ManifestStaticFilesStorage` and served by whitenoise. Fonts and icons are served from the instance.
- **Charts.** Server-rendered inline SVG. Geometry is set by attributes, which the CSP allows, and colour comes from CSS classes. The reading grid's chart and per-viewer strips share one 18-slot coordinate system, which keeps them aligned at any width.
- **JavaScript**, as separate files: `theme-init.js` (blocking, tiny, in `<head>`), then deferred `theme.js` (the toggle), `admin.js` (copy, upload progress) and `reader.js` (page turns, prefetch, keys, analytics beacons sent only while the tab is visible). The reader still works without JavaScript.
- **CSP.** `default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; manifest-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'`. The default is `'none'`, so anything not listed is refused.

### 3.11 Empty, loading and error states

- **Empty.** Each list says what will appear there and how to start. For example: "No documents yet. Upload a PDF to get started", next to the upload control. Or: "No one has opened this yet. Links you create for it appear below."
- **Loading.** The reader reserves each page's exact size from stored dimensions, so nothing moves while it loads. No spinners: pages are prefetched, and upload progress shows as a bar on the form.
- **Errors.** Shown inline, beneath the field they concern, saying what to do next. Whole-page errors (an unavailable link, a failed upload) use the same calm layout as the rest of the page.

### 3.12 Testing the front end

- **Template tests:**
  - every page sends the CSP header;
  - no template has an inline script, a `style=""` attribute or an `on*=` handler;
  - every posting form carries a CSRF token;
  - no template contains an em dash, an en dash or a middle dot.
- **A contrast test** parses the token stylesheet, resolving both sides of each `light-dark()` pair, and fails any listed text pairing below 4.5:1, for both the admin and the viewer palettes.
- **Real clicks.** Screenshots miss dead click paths. *Proposed:* Playwright (Apache-2.0) from step 4, to run the viewer flow end to end in CI.

### 3.13 Open questions (front end)

1. **Store link tokens hashed?** *Recommend yes.* The founder would see the full link only once, at creation.
2. **Accessibility of page images.** *Recommend* accepting the gap for version 1 and documenting it.
3. **Thumbnails in the reader.** *Recommend not* in version 1.
4. **Playwright in CI.** *Recommend yes*, from step 4.
5. **A privacy notice for viewers.** Under UK GDPR the founder is the controller of investors' personal data: email address, IP address and reading behaviour. *Recommend* the short statement shown in the mockup, plus an optional link to the founder's own privacy policy, set in Settings.
6. **The logo and forks.** *Decided 5 November 2026:* the logo files stay under MIT with the code, and a short `TRADEMARKS.md` asks that forks distributed as their own product use a different name and icon.

### 3.14 Logo

The Snug icon is a **half-glazed snug door**, chosen on 5 November 2026 from eight concepts. A snug is the small private room in a British pub, which suits a private room for documents and a studio named after a cocktail.

- **Construction.** It is drawn on a 200 by 200 grid in the manner of the Negroni glyph:
  - a square-topped door standing on a threshold, in round-ended 13-unit strokes;
  - a glass pane with a lowercase s cut out of it, as pub snug doors had their name etched in the glass. The cut-out is an SVG mask, so the glyph works on any background;
  - a knob, which keeps it reading as a door at small sizes;
  - the four-pointed Negroni sparkle at the top left.
- **Colourways.** Campari tile with a cream (Vermouth 100) glyph is the primary. Vermouth 500 with a Campari glyph is for dark backgrounds, and Citrus 300 with a Vermouth glyph for bright ones.
- **The lockup.** A square tile, then "Snug" in Geist Medium, as the Negroni site sets its own tile beside its wordmark. In the admin header the tile is 40px. The tile is square, an exception to the radius rule, to match Negroni's.
- **Sizes.** The full icon holds down to 32px. The favicon is a simplified drawing for 16 and 32px: no sparkle and no s, heavier strokes, and the door enlarged to fill the tile.
- **Where it appears.** The admin interface, the admin's browser tab, the README and the GitHub social preview. Never on viewer pages, which use the founder's logo, including as their favicon.
- **Inline use.** The glyph takes `currentColor`. Each inline copy on a page needs its own mask id, so the template include takes a suffix.
- **Files** are in `docs/design/logo/`:
  - `snug-glyph.svg`, the glyph alone;
  - `snug-icon-campari.svg`, `snug-icon-vermouth.svg` and `snug-icon-citrus.svg`;
  - `favicon.svg`;
  - `index.html` shows them as browsers load them, and `concepts.html`, built by `build_concepts.py`, keeps all eight concepts.

  PNG sizes (180, 192 and 512px) are generated from the SVGs when the static files are built in step 1, since nothing in the repository rasterises SVG yet.

### 3.15 Mockups

- [docs/design/mockups.html](docs/design/mockups.html) shows four screens (admin document, new link, viewer verification, reader), in light and dark and at phone width.
- It is built by [docs/design/build_mockups.py](docs/design/build_mockups.py) from `mockups.template.html`. The sample reading data lives in that script, so every figure on the page agrees.
- [docs/design/snug.css](docs/design/snug.css) holds the tokens and components.
- Fonts and icons are in `docs/design/fonts/` and `docs/design/icons/`, each with its licence.
- The mockups are not product code and will not be served by Snug.

## 4. Order of work

As the brief, with step 0 added: repository guard rails, then branch protection. Each step lands as small pull requests that leave CI green.

## 5. After version 1 (*proposed*)

**More file types, spreadsheets first.** Spreadsheets matter in a data room, especially financial models. The brief keeps Office files out of version 1, and the design leaves room for them:

- Versions record a page count and page sizes, and the viewer, watermark and analytics all work on page images. Any format that can become pages slots in without changes elsewhere; the version just needs to record its original format.
- The likely route is converting to PDF on upload, inside the same confined worker, so every safeguard applies unchanged. LibreOffice in headless mode (MPL-2.0, compatible with MIT) converts XLSX, DOCX and PPTX. It is large (several hundred MB in the image) and a big parser for hostile files, which is why it must run sandboxed like PDFium.
- Spreadsheets do not paginate naturally. Readers usually want the numbers, and often the file itself. Options to weigh then: fit each sheet to a page width; render each sheet as a scrollable image; or offer the watermarked original as a download only when the link allows downloads.
- Analytics would count sheets as pages.
