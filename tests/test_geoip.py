"""The country an address belongs to, from DB-IP's free database, kept on the server.

Housekeeping downloads the month's CSV, checks every row, and loads it into a
SQLite file of its own in the data folder. A file that fails to download or
load never replaces the one in use. No address is ever sent anywhere.
"""

import gzip
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from snug.core import geoip

ROWS = [
    "0.0.0.0,0.255.255.255,ZZ",
    "2.24.0.0,2.31.255.255,GB",
    "5.62.0.0,5.62.31.255,JE",
    "8.8.8.0,8.8.8.255,US",
    "2a00:23c0::,2a00:23c7:ffff:ffff:ffff:ffff:ffff:ffff,GB",
    "2a02:c7c::,2a02:c7c:ffff:ffff:ffff:ffff:ffff:ffff,IM",
]


def csv_gz(rows: list[str]) -> bytes:
    return gzip.compress(("\n".join(rows) + "\n").encode())


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    """The months DB-IP has published, as the download would return them."""
    published: dict[str, bytes] = {}

    def fetch(url: str) -> bytes:
        month = url.rsplit("dbip-country-lite-", 1)[1].removesuffix(".csv.gz")
        if month not in published:
            raise geoip.NotPublishedError(month)
        return published[month]

    monkeypatch.setattr(geoip, "_fetch", fetch)
    monkeypatch.setattr(geoip, "MIN_ROWS", 3)
    return published


NOV = datetime(2026, 11, 6, 15, 0, tzinfo=UTC)


@pytest.fixture
def loaded(data_folder: Path, served: dict[str, bytes]) -> None:
    served["2026-11"] = csv_gz(ROWS)
    assert geoip.refresh(NOV) == "loaded DB-IP's 2026-11 data (5 ranges)"


@pytest.mark.usefixtures("loaded")
@pytest.mark.parametrize(
    ("address", "country"),
    [
        ("2.24.0.0", "GB"),  # the first address of a range
        ("2.27.13.200", "GB"),
        ("2.31.255.255", "GB"),  # the last
        ("2.32.0.0", None),  # just past it
        ("5.62.10.1", "JE"),
        ("8.8.8.8", "US"),
        ("0.1.2.3", None),  # ZZ: reserved, so unknown
        ("2a00:23c3::1", "GB"),
        ("2a02:c7c:1::", "IM"),
        ("2001:db8::1", None),
        ("::ffff:2.24.1.1", "GB"),  # IPv4 written as IPv6
        ("not an address", None),
        ("", None),
    ],
)
def test_the_country_of_an_address(address: str, country: str | None) -> None:
    assert geoip.country(address) == country


@pytest.mark.usefixtures("loaded")
def test_it_knows_which_month_it_holds() -> None:
    assert geoip.edition() == "2026-11"


def test_without_data_every_country_is_unknown(data_folder: Path) -> None:
    assert geoip.country("2.24.0.1") is None
    assert geoip.edition() is None


@pytest.mark.usefixtures("loaded")
def test_this_month_is_not_fetched_again(served: dict[str, bytes]) -> None:
    served.clear()
    assert geoip.refresh(NOV + timedelta(days=3)) == "DB-IP's 2026-11 data is current"


@pytest.mark.usefixtures("loaded")
def test_a_new_month_replaces_the_old(served: dict[str, bytes]) -> None:
    served["2026-12"] = csv_gz([*ROWS[:3], "9.9.9.0,9.9.9.255,CH"])
    december = datetime(2026, 12, 2, 4, 0, tzinfo=UTC)
    assert geoip.refresh(december) == "loaded DB-IP's 2026-12 data (3 ranges)"
    assert geoip.edition() == "2026-12"
    assert geoip.country("9.9.9.9") == "CH"
    assert geoip.country("8.8.8.8") is None


def test_early_in_a_month_last_months_data_is_taken(
    data_folder: Path, served: dict[str, bytes]
) -> None:
    served["2026-10"] = csv_gz(ROWS)
    first = datetime(2026, 11, 1, 0, 30, tzinfo=UTC)  # November's file not out yet
    assert geoip.refresh(first) == "loaded DB-IP's 2026-10 data (5 ranges)"


@pytest.mark.usefixtures("loaded")
def test_a_failed_download_keeps_the_data_in_use(served: dict[str, bytes]) -> None:
    december = datetime(2026, 12, 2, 4, 0, tzinfo=UTC)
    assert geoip.refresh(december) == "could not fetch DB-IP's data (NotPublishedError)"
    assert geoip.edition() == "2026-11"
    assert geoip.country("8.8.8.8") == "US"


@pytest.mark.usefixtures("loaded")
def test_after_a_failure_it_waits_six_hours(served: dict[str, bytes]) -> None:
    december = datetime(2026, 12, 2, 4, 0, tzinfo=UTC)
    geoip.refresh(december)
    served["2026-12"] = csv_gz(ROWS)
    assert geoip.refresh(december + timedelta(hours=5)) == "waiting to try DB-IP again"
    assert geoip.refresh(december + timedelta(hours=6, minutes=1)).startswith("loaded")


@pytest.mark.usefixtures("loaded")
@pytest.mark.parametrize(
    "bad",
    [
        "2.24.0.0,2.31.255.255",  # no country
        "2.24.0.0,2.31.255.255,gb",  # not two capitals
        "2.24.0.0,2.31.255.255,GB,extra",
        "2.31.255.255,2.24.0.0,GB",  # backwards
        "2.24.0.0,2a00::1,GB",  # mixed families
        "two,addresses,GB",
    ],
)
def test_a_bad_row_rejects_the_whole_file(bad: str, served: dict[str, bytes]) -> None:
    served["2026-12"] = csv_gz([*ROWS, bad])
    december = datetime(2026, 12, 2, 4, 0, tzinfo=UTC)
    assert geoip.refresh(december) == "DB-IP's 2026-12 data was not usable (BadDataError)"
    assert geoip.edition() == "2026-11"


@pytest.mark.usefixtures("loaded")
def test_too_few_rows_is_refused(served: dict[str, bytes], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(geoip, "MIN_ROWS", 100_000)
    served["2026-12"] = csv_gz(ROWS)
    december = datetime(2026, 12, 2, 4, 0, tzinfo=UTC)
    assert geoip.refresh(december) == "DB-IP's 2026-12 data was not usable (BadDataError)"


@pytest.mark.usefixtures("loaded")
def test_a_corrupt_download_is_refused(served: dict[str, bytes]) -> None:
    served["2026-12"] = b"not gzip at all"
    december = datetime(2026, 12, 2, 4, 0, tzinfo=UTC)
    assert geoip.refresh(december) == "DB-IP's 2026-12 data was not usable (BadGzipFile)"
    assert geoip.edition() == "2026-11"


def test_the_download_is_https_from_db_ip_and_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: dict[str, Any] = {}

    class Response:
        status = 200

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def read(self, size: int) -> bytes:
            opened["read"] = size
            return b"x" * size

    def urlopen(request: Any, timeout: float) -> Response:
        opened["url"] = request.full_url
        opened["timeout"] = timeout
        return Response()

    monkeypatch.setattr("snug.core.outbound.urlopen", urlopen)
    with pytest.raises(geoip.BadDataError):
        geoip._fetch(geoip.SOURCE.format(month="2026-11"))
    assert opened["url"] == "https://download.db-ip.com/free/dbip-country-lite-2026-11.csv.gz"
    assert opened["read"] == geoip.MAX_DOWNLOAD + 1
    assert opened["timeout"] == 60


@pytest.mark.django_db
def test_housekeeping_refreshes_it(
    data_folder: Path, served: dict[str, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    from io import StringIO

    from django.core.management import call_command

    served["2026-11"] = csv_gz(ROWS)
    monkeypatch.setattr(geoip, "_now", lambda: NOV)
    out = StringIO()
    call_command("housekeeping", stdout=out)
    assert "GeoIP: loaded DB-IP's 2026-11 data (5 ranges)." in out.getvalue()
    assert geoip.country("5.62.0.9") == "JE"


@pytest.mark.django_db
def test_a_geoip_failure_never_stops_housekeeping(
    data_folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from io import StringIO

    from django.core.management import call_command

    from snug.core.models import SystemCheck

    def broken(now: datetime) -> str:
        raise RuntimeError("disk full")

    monkeypatch.setattr(geoip, "refresh", broken)
    out = StringIO()
    call_command("housekeeping", stdout=out)
    assert "GeoIP: failed (RuntimeError)." in out.getvalue()
    assert SystemCheck.objects.get(name="housekeeping").ok
