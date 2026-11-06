# CLAUDE.md

The working rules for this repository are in AGENTS.md, imported here so that every agent reads the same instructions.

@AGENTS.md

## Claude Code specifics

- Previewing the design mockups: `.claude/launch.json` (gitignored, per machine) can serve `docs/design/` with `python3 -m http.server`. Then open `mockups.html`, `logo/index.html` or `logo/concepts.html`.
- After changing `docs/design/mockups.template.html` or `docs/design/build_mockups.py`, rebuild with `python3 docs/design/build_mockups.py`. Do not edit `mockups.html` by hand.
- Running Snug for a browser check: a development settings file with `SNUG_DOMAIN=localhost:PORT` and, on macOS, `SNUG_UNSAFE_UNCONFINED_RENDERING=true`, served from `.claude/launch.json` with `snug serve`. Run `snug collectstatic` first after changing static files, and restart the server after code changes: gunicorn does not reload.
