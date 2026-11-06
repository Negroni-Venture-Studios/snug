# Third-party licences

Snug is MIT licensed (see [LICENSE](LICENSE)). It includes, or installs, the third-party software and assets below. Every licence is compatible with distributing Snug as MIT, and `scripts/check_licences.py` checks every installed Python package in CI.

The full licence texts ship with each package, in its `*.dist-info/licenses` folder. The container image keeps them there, under `/app/.venv/lib/python3.14/site-packages/`, with this file at `/app/THIRD_PARTY_LICENCES.md`.

## Python packages Snug runs on

| Package | Licence | Used for |
|---|---|---|
| Django (with asgiref and sqlparse) | BSD-3-Clause | The web framework |
| gunicorn | MIT | The web server |
| whitenoise | MIT | Serving static files |
| argon2-cffi (with argon2-cffi-bindings, cffi and pycparser) | MIT (cffi MIT-0, pycparser BSD-3-Clause) | Password hashing |
| pyotp | MIT | Authenticator codes |
| segno | BSD-3-Clause | QR codes for authenticator enrolment |
| pypdfium2 | Apache-2.0 or BSD-3-Clause | Reading and rendering PDFs |
| pypdf | BSD-3-Clause | Stamping downloaded PDFs |
| Pillow | MIT-CMU | Images and watermarks |

## PDFium, inside pypdfium2

pypdfium2's wheels include a compiled copy of Google's PDFium and the libraries it is built with. Their notices must travel with Snug:

| Component | Licence |
|---|---|
| PDFium | BSD-3-Clause |
| pdfium-binaries build | MIT |
| FreeType | FreeType Licence (FTL), chosen from its FTL or GPL-2.0 dual licence |
| ICU | Unicode Licence V3 |
| libjpeg-turbo | IJG Licence, BSD-3-Clause and zlib |
| Little CMS (lcms) | MIT |
| OpenJPEG | BSD-2-Clause |
| libpng | libpng Licence |
| zlib | zlib Licence |
| Anti-Grain Geometry 2.3 | AGG Licence (permissive) |
| abseil | Apache-2.0 |
| fast_float | Apache-2.0, MIT or BSL-1.0 |
| simdutf | Apache-2.0 or MIT |
| LLVM libc | Apache-2.0 with LLVM exception |

The FreeType Project licence asks that documentation mention its use: portions of this software are copyright © The FreeType Project (www.freetype.org). All rights reserved.

## Fonts and icons

| Asset | Licence | Where |
|---|---|---|
| Geist and Geist Mono, by Vercel | SIL Open Font Licence 1.1 | `snug/static/snug/fonts/`, with `OFL.txt` |
| Phosphor icons | MIT | `snug/static/snug/icons/`, with `LICENSE.txt` |
| The GitHub mark, linking to the source in the footer | GitHub's logo guidelines, which allow it for linking to a project on GitHub (https://github.com/logos) | `snug/static/snug/icons/github-mark.svg` |
| The theme toggle's sun, moon and auto icons | MIT (Feather icons) and agentlanguages.dev, by Alasdair Allan | `snug/templates/snug/partials/theme_toggle.html` |
