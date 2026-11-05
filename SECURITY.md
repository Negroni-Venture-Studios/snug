# Security policy

Snug holds founders' most sensitive documents, so we treat security reports as the highest priority.

## Reporting a vulnerability

**Please do not open a public issue.** Report privately, in either of these ways:

1. **GitHub private vulnerability reporting** (preferred). Go to the [Security tab](https://github.com/Negroni-Venture-Studios/snug/security/advisories/new) and choose "Report a vulnerability".
2. **Email** alasdair@negroniventurestudios.com.

Please include:

- what the vulnerability is and what an attacker could do with it;
- the steps to reproduce it, against a local instance if you can;
- the Snug version or commit.

You will get a response within 72 hours. We will work with you on a fix and agree the timing of any public disclosure. We are happy to credit you in the advisory.

## Scope

In scope:

- Snug's own code: authorisation, sessions, links, verification codes, file handling, rendering, analytics and the admin interface.
- The deployment files we ship: the container image, Compose file, Caddy and Litestream configuration, and cloud-init.

Out of scope:

- Weaknesses in an operator's own infrastructure or configuration that Snug's documentation already warns against.
- The limits Snug documents openly: a watermark cannot stop someone photographing a screen, and UK-only mode can be bypassed with a VPN.
- Social engineering of founders or investors.

## Supported versions

Snug has not been released yet. Once it is, security fixes go into the latest release.

## How the project guards itself

Every change to `main` goes through a pull request that must pass CI. Administrators are held to this too. CI runs:

- ruff's security rules;
- strict type checking;
- the test suite;
- gitleaks over the full git history;
- pip-audit against known vulnerabilities;
- a licence check;
- CodeQL analysis of the Python code and the workflows.

A CycloneDX software bill of materials is built on every run. Third-party GitHub Actions are pinned to commit SHAs and kept current by Dependabot. GitHub secret scanning with push protection is enabled on the repository.
