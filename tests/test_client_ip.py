"""X-Forwarded-For is believed only from a trusted proxy."""

import ipaddress

import pytest
from django.conf import settings
from django.test import RequestFactory

from snug.core.client import client_ip, user_agent

CADDY = "172.30.0.2"


@pytest.fixture(autouse=True)
def caddy_is_trusted(monkeypatch: pytest.MonkeyPatch) -> None:
    config = settings.CONFIG
    networks = (ipaddress.ip_network("172.30.0.0/24"), ipaddress.ip_network("127.0.0.1/32"))
    monkeypatch.setattr(
        settings, "CONFIG", type(config)(**{**config.__dict__, "trusted_proxies": networks})
    )


def ip(remote: str, forwarded: str | None = None) -> str | None:
    request = RequestFactory().get("/")
    request.META["REMOTE_ADDR"] = remote
    if forwarded is not None:
        request.META["HTTP_X_FORWARDED_FOR"] = forwarded
    return client_ip(request)


def test_direct_clients_are_believed() -> None:
    assert ip("198.51.100.7") == "198.51.100.7"


def test_forwarded_header_from_an_untrusted_peer_is_ignored() -> None:
    assert ip("198.51.100.7", "203.0.113.1") == "198.51.100.7"


def test_trusted_proxy_gives_the_client_it_saw() -> None:
    assert ip(CADDY, "198.51.100.7") == "198.51.100.7"


def test_addresses_the_client_wrote_itself_are_ignored() -> None:
    # The client sent "X-Forwarded-For: 10.0.0.1"; Caddy appended the real address.
    assert ip(CADDY, "10.0.0.1, 198.51.100.7") == "198.51.100.7"


def test_chains_of_trusted_proxies_are_walked() -> None:
    assert ip(CADDY, "198.51.100.7, 127.0.0.1") == "198.51.100.7"


def test_ipv6_clients() -> None:
    assert ip(CADDY, "2001:db8::1") == "2001:db8::1"


@pytest.mark.parametrize("forwarded", ["not-an-ip", "198.51.100.7, garbage", ""])
def test_unparseable_chains_give_no_address(forwarded: str) -> None:
    assert ip(CADDY, forwarded) in (None, CADDY)
    if forwarded:
        assert ip(CADDY, forwarded) is None


def test_trusted_proxy_without_a_header_is_itself() -> None:
    assert ip(CADDY) == CADDY


def test_unparseable_peer() -> None:
    assert ip("") is None


def test_user_agent_is_truncated() -> None:
    request = RequestFactory().get("/", HTTP_USER_AGENT="x" * 1000)
    assert len(user_agent(request)) == 512
