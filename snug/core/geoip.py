"""Which country an address is in, from DB-IP's free IP to Country Lite database.

The data is DB-IP's (https://db-ip.com), licensed CC BY 4.0. Housekeeping
downloads each month's CSV from DB-IP, checks every row, and loads it into
geoip/countries.sqlite3 in the data folder, apart from Snug's own database
and its backups, since it can always be fetched again. Lookups read that
file and nothing else: no address ever leaves the server.

A download or a file that fails any check never replaces the data in use,
and after a failure Snug waits six hours before trying again.
"""

import contextlib
import csv
import gzip
import io
import ipaddress
import sqlite3
import urllib.request
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

from django.conf import settings

SOURCE = "https://download.db-ip.com/free/dbip-country-lite-{month}.csv.gz"
ATTRIBUTION = "IP geolocation by DB-IP"
ATTRIBUTION_URL = "https://db-ip.com"
# The November 2026 file is 4.6 MB with 732,796 ranges.
MAX_DOWNLOAD = 64 * 1024 * 1024
MIN_ROWS = 100_000
RETRY_AFTER = timedelta(hours=6)
_IPV4_MAPPED = 0xFFFF00000000

Address = ipaddress.IPv4Address | ipaddress.IPv6Address


class NotPublishedError(Exception):
    """DB-IP has no file for that month (yet)."""


class BadDataError(Exception):
    """The download is not a file Snug can trust."""


def _folder() -> Path:
    return Path(settings.CONFIG.data_dir) / "geoip"


def _path() -> Path:
    return _folder() / "countries.sqlite3"


def _key(address: Address) -> bytes:
    """16 bytes, big-endian, with IPv4 mapped into IPv6, so ranges compare as bytes."""
    if isinstance(address, ipaddress.IPv4Address):
        return ipaddress.IPv6Address(int(address) + _IPV4_MAPPED).packed
    if address.ipv4_mapped is not None:
        return _key(address.ipv4_mapped)
    return address.packed


def _now() -> datetime:
    return datetime.now(UTC)


def _open() -> sqlite3.Connection | None:
    path = _path()
    if not path.exists():
        return None
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def country(address: str) -> str | None:
    """The address's two-letter country code, or None when unknown or without data."""
    try:
        key = _key(ipaddress.ip_address(address.strip()))
    except ValueError:
        return None
    connection = _open()
    if connection is None:
        return None
    with contextlib.closing(connection):
        row = connection.execute(
            "SELECT last, country FROM ranges WHERE first <= ? ORDER BY first DESC LIMIT 1",
            (key,),
        ).fetchone()
    if row is None or row[0] < key:
        return None
    return str(row[1])


def edition() -> str | None:
    """The month of the data in use, like "2026-11", or None without any."""
    connection = _open()
    if connection is None:
        return None
    with contextlib.closing(connection):
        row = connection.execute("SELECT value FROM meta WHERE name = 'edition'").fetchone()
    return str(row[0]) if row else None


def _fetch(url: str) -> bytes:
    # Only ever SOURCE, an https URL.
    request = urllib.request.Request(url, headers={"User-Agent": "Snug (self-hosted)"})  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - fixed https URL
            data: bytes = response.read(MAX_DOWNLOAD + 1)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise NotPublishedError(url) from error
        raise
    if len(data) > MAX_DOWNLOAD:
        raise BadDataError("the download is too large")
    return data


def _ranges(compressed: bytes) -> Iterator[tuple[bytes, bytes, str]]:
    """Every range, checked as it is read. Any bad row rejects the whole file."""
    text = io.TextIOWrapper(gzip.GzipFile(fileobj=io.BytesIO(compressed)), encoding="ascii")
    for fields in csv.reader(text):
        if len(fields) != 3:
            raise BadDataError("a row without three fields")
        first_text, last_text, code = fields
        if len(code) != 2 or not code.isascii() or not code.isupper():
            raise BadDataError("a country that is not two capital letters")
        try:
            first, last = ipaddress.ip_address(first_text), ipaddress.ip_address(last_text)
        except ValueError as error:
            raise BadDataError("a row with an address that is not one") from error
        if first.version != last.version or first > last:  # type: ignore[operator]
            raise BadDataError("a range that runs backwards or mixes IPv4 and IPv6")
        if code != "ZZ":  # DB-IP's mark for reserved and unknown ranges
            yield _key(first), _key(last), code


def _load(compressed: bytes, month: str) -> int:
    """Build a new file beside the old, a batch at a time, and swap it in only once
    every row has passed. The whole file is never held in memory: a small server
    has none to spare. Returns the number of ranges."""
    folder = _folder()
    folder.mkdir(mode=0o700, exist_ok=True)
    building = folder / "countries.sqlite3.new"
    building.unlink(missing_ok=True)
    try:
        with contextlib.closing(sqlite3.connect(building)) as connection:
            connection.execute("PRAGMA journal_mode = OFF")
            connection.execute(
                "CREATE TABLE ranges (first BLOB PRIMARY KEY, last BLOB NOT NULL, "
                "country TEXT NOT NULL) WITHOUT ROWID"
            )
            connection.execute("CREATE TABLE meta (name TEXT PRIMARY KEY, value TEXT NOT NULL)")
            count = 0
            batch: list[tuple[bytes, bytes, str]] = []
            for row in _ranges(compressed):
                batch.append(row)
                if len(batch) == 10_000:
                    connection.executemany("INSERT INTO ranges VALUES (?, ?, ?)", batch)
                    count += len(batch)
                    batch.clear()
            connection.executemany("INSERT INTO ranges VALUES (?, ?, ?)", batch)
            count += len(batch)
            if count < MIN_ROWS:
                raise BadDataError("too few ranges")
            connection.execute("INSERT INTO meta VALUES ('edition', ?)", (month,))
            connection.commit()
    except BaseException:
        building.unlink(missing_ok=True)
        raise
    building.replace(_path())
    return count


# What a download that is not DB-IP's data, whole and well formed, raises.
_UNUSABLE = (
    BadDataError,
    gzip.BadGzipFile,
    EOFError,
    UnicodeDecodeError,
    csv.Error,
    sqlite3.IntegrityError,
)


def _attempted(now: datetime) -> bool:
    """Record a try, saying whether one was made in the last six hours."""
    folder = _folder()
    folder.mkdir(mode=0o700, exist_ok=True)
    marker = folder / "last-attempt"
    with contextlib.suppress(OSError, ValueError):
        last = datetime.fromisoformat(marker.read_text(encoding="utf-8").strip())
        if now - last < RETRY_AFTER:
            return True
    marker.write_text(now.isoformat(), encoding="utf-8")
    return False


def refresh(now: datetime) -> str:
    """Fetch this month's data if it is not already in use. Says what happened."""
    month = f"{now:%Y-%m}"
    held = edition()
    if held == month:
        return f"DB-IP's {month} data is current"
    if _attempted(now):
        return "waiting to try DB-IP again"
    previous = f"{(now.replace(day=1) - timedelta(days=1)):%Y-%m}"
    candidates = [month] if held == previous else [month, previous]
    for candidate in candidates:
        try:
            compressed = _fetch(SOURCE.format(month=candidate))
        except NotPublishedError:
            continue
        except OSError as error:
            return f"could not fetch DB-IP's data ({type(error).__name__})"
        try:
            count = _load(compressed, candidate)
        except (
            BadDataError,
            EOFError,
            UnicodeDecodeError,
            csv.Error,
            sqlite3.IntegrityError,
        ) as error:
            return f"DB-IP's {candidate} data was not usable ({type(error).__name__})"
        except gzip.BadGzipFile as error:
            return f"DB-IP's {candidate} data was not usable ({type(error).__name__})"
        return f"loaded DB-IP's {candidate} data ({count:,} ranges)"
    return f"could not fetch DB-IP's data ({NotPublishedError.__name__})"
