# Contributing to Snug

Thank you for helping. Snug guards founders' most sensitive documents, so correct authorisation matters more than features, and every change is held to that.

## Before you start

- Read [DESIGN.md](DESIGN.md). It records what we are building and why, and it wins over the original brief in `docs/BRIEF.md`.
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

## Everyday commands

```bash
uv run pytest                      # tests
uv run pytest --cov                # tests with the coverage floor
uv run ruff check .                # lint, including security rules
uv run ruff format .               # format
uv run mypy                        # strict type checking
uv run pre-commit run --all-files  # everything CI runs, locally
```

## How changes land

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
- Force pushes and deletion of `main` are blocked.
- CI is the gate; no approving review count is required. Stale reviews are dismissed when new commits arrive.

Merge with a squash or a merge commit. Branches are deleted after merging.

## Standards

- **Test first.** Write the test that proves your change, watch it fail for the reason you expect, then make it pass. A test that passes both before and after a change proves nothing about it.
- **Security requirements are requirements.** DESIGN.md and the brief list them: one authorisation path for viewer content, no database keys in viewer URLs, no CORS, a strict content security policy, CSRF protection, rate limits, safe file handling, and no secrets in logs. Each has tests; keep them passing and extend them.
- **Type hints throughout**, checked by mypy in strict mode.
- **No inline scripts, inline styles or `on*=` handlers** in templates. The content security policy forbids them.
- **British English** in documentation and interface text. In interface text: sentence case, no em or en dashes, and no middle dots used as separators.
- **Commit messages** use the imperative mood ("Add link revocation", not "Added …"), with a first line under 72 characters.
- **Changelog.** Add a line under `[Unreleased]` in CHANGELOG.md for anything a user or operator would notice.

## Dependencies

Snug keeps its dependencies few and well maintained.

- **Ask before adding one.** Open an issue or say so in the pull request, with the reason and its licence.
- Every dependency, direct or transitive, must have a licence compatible with distributing Snug as MIT. `scripts/check_licences.py` enforces this in CI. GPL and AGPL are ruled out, which is why Snug uses pypdfium2 rather than PyMuPDF, Ghostscript or Poppler.
- Pin new tools and actions to releases at least two weeks old. The exception is a security release from an upstream we already depend on, which we take at once.

## Code of conduct

Everyone taking part is expected to follow the [code of conduct](CODE_OF_CONDUCT.md).

## Licence

By contributing, you agree that your contributions are licensed under the [MIT licence](LICENSE).
