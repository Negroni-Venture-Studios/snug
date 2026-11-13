# Contributing to Snug

Thank you for helping. Snug holds founders' most sensitive documents, so correct authorisation comes before features.

## Before you start

- Read [docs/architecture.md](docs/architecture.md) for how the code works, and [docs/design.md](docs/design.md) for what Snug is and the decisions behind it. Planned work is in the [issues labelled enhancement](https://github.com/Negroni-Venture-Studios/snug/issues?q=is%3Aissue+is%3Aopen+label%3Aenhancement).
- For anything larger than a small fix, open an issue first so we can agree the approach.
- Report security problems privately, as [SECURITY.md](SECURITY.md) describes, never in a public issue.

## Setting up

You need [uv](https://docs.astral.sh/uv/) and git. uv installs the right Python for you.

```bash
git clone https://github.com/Negroni-Venture-Studios/snug.git
cd snug
uv sync
uv run pre-commit install
```

To run Snug itself while you work, follow [Running Snug on your own computer](docs/configuration.md#running-snug-on-your-own-computer). On macOS, add `SNUG_UNSAFE_UNCONFINED_RENDERING=true` to your development settings. Snug reads uploads only inside a Linux Landlock confinement, which macOS lacks, and refuses otherwise. Never set it on a server. The tests set it for themselves on macOS, and CI runs them confined on Linux.

## Everyday commands

```bash
uv run pytest                      # tests
uv run pytest --cov                # tests with the coverage floor
uv run playwright install chromium  # once, for the end-to-end tests
SNUG_E2E=1 uv run pytest tests/e2e --ds=tests.e2e_settings  # a real browser, as CI runs it
uv run ruff check .                # lint, including security rules
uv run ruff format .               # format
uv run mypy                        # strict type checking
uv run pre-commit run --all-files  # the fast checks; CI also runs coverage, the browser tests and the security scans
```

Rebuild the documentation's pictures when a page or a diagram changes:

```bash
python3 docs/diagrams/build.py     # the diagrams, from docs/diagrams/*.svg
SNUG_E2E=1 SNUG_SCREENSHOTS=1 uv run pytest tests/e2e/test_screenshots.py --ds=tests.e2e_settings
```

The second builds a demo company's data room (`tests/demo.py`), opens each page in light and dark in Chromium, and writes the framed screenshots to `docs/images/screenshots/`.

## How a change gets into `main`

`main` is protected:

- Every change arrives through a pull request. Nobody pushes to `main` directly, maintainers included.
- These CI checks must pass, on a branch that is up to date with `main`:
  - `lint`
  - `typecheck`
  - the `test` jobs
  - `security`
  - `dependency-audit`
  - `sbom`
  - the CodeQL `analyze` jobs
  - `e2e (real browser)`, the investor's journey and the admin controls in Chromium through Playwright
  - `image smoke test`, which builds the container image and checks that its document worker renders a PDF page confined
  - `office converter`, which builds the LibreOffice converter's image and converts a Word document, a deck and a workbook under gVisor
- Force pushes and deletion of `main` are blocked.
- CI is the gate; no approving review count is required. Stale reviews are dismissed when new commits arrive.

Merge with a squash or a merge commit. Branches are deleted after merging.

## Standards

- **Test first.** Write the test that proves your change, watch it fail for the reason you expect, then make it pass. A test that passes both before and after a change proves nothing about it.
- **Keep to the security requirements.** [AGENTS.md](AGENTS.md) lists them: one authorisation path for viewer content, no database keys in viewer URLs, no CORS, a strict content security policy, CSRF protection, rate limits, safe file handling, and no secrets in logs. Each has tests; keep them passing and extend them.
- **Type hints throughout**, checked by mypy in strict mode.
- **No inline scripts, inline styles or `on*=` handlers** in templates. The content security policy forbids them.
- **Calls out of Snug** over HTTP go only to Google and DB-IP, through `snug/core/outbound.py`, apart from `snug doctor`, and housekeeping every hour, checking Snug's own domain. The tests never reach the internet.
- **British English** in documentation and interface text. In interface text: sentence case, no em or en dashes, and no middle dots used as separators.
- **Commit messages** use the imperative mood ("Add link revocation", not "Added …"), with a first line under 72 characters.
- **Changelog.** Add a line under `[Unreleased]` in CHANGELOG.md for anything a user or operator would notice.

## Dependencies

Snug keeps its dependencies few and well maintained.

- **Ask before adding one.** Open an issue or say so in the pull request, with the reason and its licence.
- Every dependency, direct or transitive, must have a licence compatible with distributing Snug as MIT. `scripts/check_licences.py` enforces this in CI. GPL and AGPL are ruled out, which is why Snug uses pypdfium2 rather than PyMuPDF, Ghostscript or Poppler.
- Pin new tools and actions to releases at least two weeks old. The exception is a security release from an upstream we already depend on, which we take at once.

## Releasing

1. On a branch named `release-X.Y.Z`, with a pull request to `main`, set the version in `pyproject.toml` (then run `uv lock`), name that version's images, Snug's and the converter's, in `deploy/compose.yaml`, and date its section in `CHANGELOG.md`. `tests/test_release.py` checks that `pyproject.toml`, Snug's image and the changelog agree.
2. Check that the README's Status section, `docs/deploy-localhost.md` and `docs/deploy-digitalocean.md` describe the release.
3. Tag a release candidate, `vX.Y.Z-rc.N`, on the branch. The `Image` and `Office image` workflows publish its images as `:X.Y.Z-rc.N`, and `:latest` does not move. Set up a fresh server by following the DigitalOcean guide, with the startup script changed to download the candidate's tag and to add two lines to `snug.env`, setting `SNUG_IMAGE` and `SNUG_OFFICE_IMAGE` to the candidate's images. To try the update from the previous release, add the same two lines to a server running it, then run `sudo env SNUG_REF=vX.Y.Z-rc.N snug update`.
4. Merge the pull request, tag `vX.Y.Z` on the merge commit, and publish a GitHub release for the tag. New servers and `snug update` use the newest published release, so they stay on the previous one until this one is published. On any server that ran a candidate, delete the two image lines from `snug.env` and run `sudo snug update`.

## Code of conduct

Everyone taking part is expected to follow the [code of conduct](CODE_OF_CONDUCT.md).

## Licence

By contributing, you agree that your contributions are licensed under the [MIT licence](LICENSE).
