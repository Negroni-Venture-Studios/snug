"""Snug's configuration, read from the environment.

All configuration comes from one environment file, documented in
docs/configuration.md. Docker Compose loads that file into the environment;
for local work, `snug --env-file PATH` does the same. Every problem is
reported at once, before Snug starts, so an operator can fix them in one go
rather than one restart at a time.
"""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

MIN_SECRET_LENGTH = 50
MIN_SECRET_DISTINCT = 20
DEFAULT_DATA_DIR = "/var/lib/snug"

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}
_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)(?:localhost|(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63})$"
)
_ENV_LINE = re.compile(r"^(?:export\s+)?([A-Z_][A-Z0-9_]*)=(.*)$")


class ConfigError(Exception):
    """One or more settings are missing or invalid. Lists every problem."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        lines = "\n".join(f"  - {p}" for p in problems)
        super().__init__(f"Snug cannot start. Fix these settings:\n{lines}")


@dataclass(frozen=True)
class Config:
    secret_key: str
    domain: str
    data_dir: Path
    https: bool
    debug: bool

    @property
    def database_path(self) -> Path:
        return self.data_dir / "snug.sqlite3"

    @property
    def origin(self) -> str:
        return f"{'https' if self.https else 'http'}://{self.domain}"

    def __repr__(self) -> str:
        # Never let the secret key reach a log or a traceback.
        return (
            f"Config(domain={self.domain!r}, data_dir={str(self.data_dir)!r}, "
            f"https={self.https}, debug={self.debug}, secret_key=<hidden>)"
        )


def _flag(env: Mapping[str, str], name: str, default: bool, problems: list[str]) -> bool:
    raw = env.get(name, "").strip().lower()
    if raw == "":
        return default
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    problems.append(f"{name} must be true or false, not {raw!r}.")
    return default


def load_config(env: Mapping[str, str] | None = None) -> Config:
    """Read and check Snug's settings. Raises ConfigError listing every problem."""
    if env is None:
        env = os.environ
    problems: list[str] = []

    secret_key = env.get("SNUG_SECRET_KEY", "")
    if not secret_key:
        problems.append(
            "SNUG_SECRET_KEY is not set. Generate one with: "
            'python3 -c "import secrets; print(secrets.token_urlsafe(64))"'
        )
    elif len(secret_key) < MIN_SECRET_LENGTH or len(set(secret_key)) < MIN_SECRET_DISTINCT:
        problems.append(
            f"SNUG_SECRET_KEY is too weak: use at least {MIN_SECRET_LENGTH} random characters."
        )

    domain = env.get("SNUG_DOMAIN", "").strip().lower()
    if not domain:
        problems.append("SNUG_DOMAIN is not set. Use the bare host name, like room.example.com.")
    elif not _HOSTNAME.match(domain):
        problems.append(
            f"SNUG_DOMAIN must be a bare host name, like room.example.com, not {domain!r}."
        )

    data_dir = Path(env.get("SNUG_DATA_DIR", "").strip() or DEFAULT_DATA_DIR)
    if not data_dir.is_absolute():
        problems.append(f"SNUG_DATA_DIR must be an absolute path, not {str(data_dir)!r}.")

    https = _flag(env, "SNUG_HTTPS", default=True, problems=problems)
    debug = _flag(env, "SNUG_DEBUG", default=False, problems=problems)

    # Plain HTTP and debug pages are for a developer's own machine only. On a
    # real domain they would expose cookies and internals to the internet.
    if domain and domain != "localhost":
        if not https:
            problems.append("SNUG_HTTPS can only be false when SNUG_DOMAIN is localhost.")
        if debug:
            problems.append("SNUG_DEBUG can only be true when SNUG_DOMAIN is localhost.")

    if problems:
        raise ConfigError(problems)
    return Config(secret_key=secret_key, domain=domain, data_dir=data_dir, https=https, debug=debug)


def read_env_file(path: Path) -> dict[str, str]:
    """Parse a KEY=VALUE environment file, the format Docker Compose reads.

    Blank lines and lines starting with # are ignored. Values may be wrapped in
    single or double quotes. There is no variable interpolation.
    """
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _ENV_LINE.match(line)
        if not match:
            # Do not echo the line: it may hold a secret.
            raise ConfigError([f"{path}, line {number}: expected KEY=value."])
        key, value = match.group(1), match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values
