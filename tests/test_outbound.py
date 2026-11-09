"""Snug's own calls out, to Google and to DB-IP.

They take proxy settings from the environment only. On macOS, urllib otherwise
asks the system for them through Apple's frameworks, which refuse to run in a
process forked from another (as gunicorn starts its workers) and kill it: the
browser saw an empty response when Google sent the admin back to Snug.
"""

import urllib.request
from typing import Any

import pytest

from snug.core import geoip, gmail, outbound

# The real functions, before the suite's fixture stops downloads. These tests
# open only data: URLs, which never leave the process.
REAL_URLOPEN = urllib.request.urlopen
REAL_OPEN = outbound.urlopen


@pytest.fixture
def no_system_proxies(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("asked the system for its proxy settings")

    monkeypatch.setattr(urllib.request, "urlopen", REAL_URLOPEN)
    monkeypatch.setattr(outbound, "urlopen", REAL_OPEN)
    monkeypatch.setattr(urllib.request, "getproxies", refuse)
    monkeypatch.setattr(urllib.request, "proxy_bypass", refuse)
    monkeypatch.setattr(urllib.request, "_opener", None)  # no opener built earlier
    for name in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.usefixtures("no_system_proxies")
def test_google_is_called_without_the_system_proxy_settings() -> None:
    assert gmail._request("data:application/json,%7B%22ok%22%3Atrue%7D", form={"a": "b"}) == {
        "ok": True
    }


@pytest.mark.usefixtures("no_system_proxies")
def test_geoip_downloads_without_the_system_proxy_settings() -> None:
    assert geoip._fetch("data:text/plain,hello") == b"hello"


def test_a_proxy_in_the_environment_is_still_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.fernlea.example:3128")
    opener: Any = outbound._opener()
    found: list[Any] = [h for h in opener.handlers if isinstance(h, urllib.request.ProxyHandler)]
    (proxies,) = found
    assert proxies.proxies["https"] == "http://proxy.fernlea.example:3128"
