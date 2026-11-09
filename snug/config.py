"""Snug's configuration, read from the environment.

All configuration comes from one environment file, documented in
docs/configuration.md. Docker Compose loads that file into the environment;
for local work, `snug --env-file PATH` does the same. Every problem is
reported at once, before Snug starts, so an operator can fix them in one go
rather than one restart at a time.
"""

import ipaddress
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

MIN_SECRET_LENGTH = 50
MIN_SECRET_DISTINCT = 20
DEFAULT_DATA_DIR = "/var/lib/snug"
DEFAULT_TRUSTED_PROXIES = "127.0.0.1/32, ::1/128"
DEFAULT_MAX_UPLOAD_MB = 100
MAX_UPLOAD_MB_CEILING = 1024
# Videos have their own limit: a few minutes of 1080p is hundreds of megabytes.
DEFAULT_MAX_VIDEO_MB = 1024
MAX_VIDEO_MB_CEILING = 8192
DEFAULT_VIEWER_SESSION_DAYS = 7
MAX_VIEWER_SESSION_DAYS = 90
SMTP_SECURITY = ("starttls", "tls", "none")
# The ports mail providers accept submissions on, for snug.env and Settings alike;
# anything else could aim Snug's SMTP client at some other service. See snug.core.mail.
SMTP_PORTS = (25, 465, 587, 2465, 2525, 2587)
_EMAIL = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")

IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}
# A host name and a port, like localhost:8000. Anything else with a colon, such
# as a URL, is left to the host-name check below.
_WITH_PORT = re.compile(r"^([a-z0-9.-]+):([^:/]*)$")
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
    # Proxies whose X-Forwarded-For header is believed: Caddy, and only Caddy.
    trusted_proxies: tuple[IPNetwork, ...] = ()
    max_upload_mb: int = DEFAULT_MAX_UPLOAD_MB
    max_video_mb: int = DEFAULT_MAX_VIDEO_MB
    viewer_session_days: int = DEFAULT_VIEWER_SESSION_DAYS
    company_name: str = ""
    email_from: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_security: str = "starttls"
    # Development only: read PDFs even where the worker cannot be confined.
    unsafe_unconfined_rendering: bool = False
    # localhost only, when the container is published on a port: links need it.
    port: int | None = None

    @property
    def database_path(self) -> Path:
        return self.data_dir / "snug.sqlite3"

    @property
    def origin(self) -> str:
        port = f":{self.port}" if self.port else ""
        return f"{'https' if self.https else 'http'}://{self.domain}{port}"

    @property
    def email_outbox(self) -> bool:
        """On localhost without SMTP, emails go to a folder, for development."""
        return not self.smtp_host and self.domain == "localhost"

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_host) or self.email_outbox

    def __repr__(self) -> str:
        # Never let the secret key or SMTP password reach a log or a traceback.
        return (
            f"Config(domain={self.domain!r}, data_dir={str(self.data_dir)!r}, "
            f"https={self.https}, debug={self.debug}, smtp_host={self.smtp_host!r}, "
            "secret_key=<hidden>, smtp_password=<hidden>)"
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
    port: int | None = None
    with_port = _WITH_PORT.match(domain)
    if with_port:
        domain, raw_port = with_port.groups()
        if domain != "localhost":
            problems.append(
                "SNUG_DOMAIN can only include a port when it is localhost, like "
                "localhost:8000. A real domain is served by Caddy on the standard port."
            )
        elif not raw_port.isdecimal() or not 1 <= int(raw_port) <= 65535:
            problems.append(
                f"SNUG_DOMAIN's port must be a number from 1 to 65535, not {raw_port!r}."
            )
        else:
            port = int(raw_port)
    if not domain:
        problems.append("SNUG_DOMAIN is not set. Use the bare host name, like room.example.com.")
    elif not _HOSTNAME.match(domain):
        problems.append(
            f"SNUG_DOMAIN must be a bare host name, like room.example.com, not {domain!r}."
        )

    data_dir = Path(env.get("SNUG_DATA_DIR", "").strip() or DEFAULT_DATA_DIR)
    if not data_dir.is_absolute():
        problems.append(f"SNUG_DATA_DIR must be an absolute path, not {str(data_dir)!r}.")

    trusted_proxies = _networks(env, "SNUG_TRUSTED_PROXIES", problems)

    raw_upload = env.get("SNUG_MAX_UPLOAD_MB", "").strip()
    max_upload_mb = DEFAULT_MAX_UPLOAD_MB
    if raw_upload:
        if raw_upload.isdecimal() and 1 <= int(raw_upload) <= MAX_UPLOAD_MB_CEILING:
            max_upload_mb = int(raw_upload)
        else:
            problems.append(
                f"SNUG_MAX_UPLOAD_MB must be a whole number of megabytes from 1 to "
                f"{MAX_UPLOAD_MB_CEILING}, not {raw_upload!r}."
            )

    raw_video = env.get("SNUG_MAX_VIDEO_MB", "").strip()
    max_video_mb = DEFAULT_MAX_VIDEO_MB
    if raw_video:
        if raw_video.isdecimal() and 1 <= int(raw_video) <= MAX_VIDEO_MB_CEILING:
            max_video_mb = int(raw_video)
        else:
            problems.append(
                f"SNUG_MAX_VIDEO_MB must be a whole number of megabytes from 1 to "
                f"{MAX_VIDEO_MB_CEILING}, not {raw_video!r}."
            )

    raw_days = env.get("SNUG_VIEWER_SESSION_DAYS", "").strip()
    viewer_session_days = DEFAULT_VIEWER_SESSION_DAYS
    if raw_days:
        if raw_days.isdecimal() and 1 <= int(raw_days) <= MAX_VIEWER_SESSION_DAYS:
            viewer_session_days = int(raw_days)
        else:
            problems.append(
                f"SNUG_VIEWER_SESSION_DAYS must be a whole number of days from 1 to "
                f"{MAX_VIEWER_SESSION_DAYS}, not {raw_days!r}."
            )

    company_name = " ".join(env.get("SNUG_COMPANY_NAME", "").split())
    if len(company_name) > 100:
        problems.append("SNUG_COMPANY_NAME must be 100 characters or fewer.")

    smtp_host = env.get("SNUG_SMTP_HOST", "").strip()
    email_from = env.get("SNUG_EMAIL_FROM", "").strip()
    smtp_security = env.get("SNUG_SMTP_SECURITY", "").strip().lower() or "starttls"
    raw_port = env.get("SNUG_SMTP_PORT", "").strip()
    smtp_port = 465 if smtp_security == "tls" else 587
    if raw_port:
        if raw_port.isdecimal() and 1 <= int(raw_port) <= 65535:
            smtp_port = int(raw_port)
        else:
            problems.append(f"SNUG_SMTP_PORT must be a port number, not {raw_port!r}.")
    if smtp_host and domain != "localhost" and smtp_port not in SMTP_PORTS:
        problems.append(
            f"SNUG_SMTP_PORT must be {', '.join(map(str, SMTP_PORTS[:-1]))} or "
            f"{SMTP_PORTS[-1]}, not {smtp_port}."
        )
    if smtp_security not in SMTP_SECURITY:
        problems.append(f"SNUG_SMTP_SECURITY must be starttls, tls or none, not {smtp_security!r}.")
    if smtp_host and not email_from:
        problems.append(
            "SNUG_EMAIL_FROM is not set. Use an address on your own domain, "
            "like rooms@example.com. See docs/email.md."
        )
    if email_from and not _EMAIL.match(email_from):
        problems.append(f"SNUG_EMAIL_FROM must be an email address, not {email_from!r}.")

    https = _flag(env, "SNUG_HTTPS", default=True, problems=problems)
    debug = _flag(env, "SNUG_DEBUG", default=False, problems=problems)
    unsafe_unconfined_rendering = _flag(
        env, "SNUG_UNSAFE_UNCONFINED_RENDERING", default=False, problems=problems
    )

    # Plain HTTP and debug pages are for a developer's own machine only. On a
    # real domain they would expose cookies and internals to the internet.
    if domain and domain != "localhost":
        if not https:
            problems.append("SNUG_HTTPS can only be false when SNUG_DOMAIN is localhost.")
        if debug:
            problems.append("SNUG_DEBUG can only be true when SNUG_DOMAIN is localhost.")
        if smtp_host and smtp_security == "none":
            problems.append(
                "SNUG_SMTP_SECURITY can only be none when SNUG_DOMAIN is localhost: "
                "codes and links must not cross the internet unencrypted."
            )

    if problems:
        raise ConfigError(problems)
    return Config(
        secret_key=secret_key,
        domain=domain,
        port=port,
        data_dir=data_dir,
        https=https,
        debug=debug,
        trusted_proxies=trusted_proxies,
        max_upload_mb=max_upload_mb,
        max_video_mb=max_video_mb,
        viewer_session_days=viewer_session_days,
        company_name=company_name,
        email_from=email_from,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_username=env.get("SNUG_SMTP_USERNAME", "").strip(),
        smtp_password=env.get("SNUG_SMTP_PASSWORD", ""),
        smtp_security=smtp_security,
        unsafe_unconfined_rendering=unsafe_unconfined_rendering,
    )


def _networks(env: Mapping[str, str], name: str, problems: list[str]) -> tuple[IPNetwork, ...]:
    raw = env.get(name, "").strip() or DEFAULT_TRUSTED_PROXIES
    networks: list[IPNetwork] = []
    for item in (part.strip() for part in raw.split(",")):
        if not item:
            continue
        try:
            network = ipaddress.ip_network(item, strict=False)
        except ValueError:
            problems.append(f"{name} must be a list of IP addresses or networks, not {item!r}.")
            continue
        if network.prefixlen == 0:
            # Trusting every address would let anyone claim any client address.
            problems.append(f"{name} cannot trust every address ({item}).")
            continue
        networks.append(network)
    return tuple(networks)


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
