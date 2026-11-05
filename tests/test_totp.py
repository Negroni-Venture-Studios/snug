"""TOTP: the drift window, replay protection and code formats."""

from datetime import UTC, datetime, timedelta

import pyotp
import pytest

from snug.accounts import totp

NOW = datetime(2026, 11, 5, 12, 0, 15, tzinfo=UTC)
SECRET = totp.new_secret()
TOTP = pyotp.TOTP(SECRET)
STEP = TOTP.timecode(NOW)


def code_at(offset_steps: int) -> str:
    return TOTP.generate_otp(STEP + offset_steps)


def test_new_secrets_are_160_bit_and_distinct() -> None:
    secrets = {totp.new_secret() for _ in range(50)}
    assert len(secrets) == 50
    assert all(len(s) == 32 for s in secrets)


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_codes_within_one_step_of_now_are_accepted(offset: int) -> None:
    assert totp.verify(SECRET, code_at(offset), last_step=0, now=NOW) == STEP + offset


@pytest.mark.parametrize("offset", [-3, -2, 2, 3])
def test_codes_outside_the_window_are_refused(offset: int) -> None:
    assert totp.verify(SECRET, code_at(offset), last_step=0, now=NOW) is None


def test_a_code_cannot_be_replayed() -> None:
    step = totp.verify(SECRET, code_at(0), last_step=0, now=NOW)
    assert step == STEP
    assert totp.verify(SECRET, code_at(0), last_step=step, now=NOW) is None
    # Nor can an earlier code from the window once a later one was used.
    assert totp.verify(SECRET, code_at(-1), last_step=step, now=NOW) is None
    # The next step still works.
    assert totp.verify(SECRET, code_at(1), last_step=step, now=NOW) == STEP + 1


def test_spaces_in_codes_are_ignored() -> None:
    code = code_at(0)
    assert totp.verify(SECRET, f" {code[:3]} {code[3:]} ", last_step=0, now=NOW) == STEP


@pytest.mark.parametrize("code", ["", "12345", "1234567", "abcdef", "12345a", "١٢٣٤٥٦"])
def test_malformed_codes_are_refused(code: str) -> None:
    assert totp.verify(SECRET, code, last_step=0, now=NOW) is None


def test_another_secret_does_not_verify() -> None:
    assert totp.verify(totp.new_secret(), code_at(0), last_step=0, now=NOW) is None


def test_blank_secret_never_verifies() -> None:
    assert totp.verify("", code_at(0), last_step=0, now=NOW) is None


def test_default_clock_is_now() -> None:
    live = pyotp.TOTP(SECRET).now()
    assert totp.verify(SECRET, live, last_step=0) is not None


def test_provisioning_uri_names_the_instance() -> None:
    uri = totp.provisioning_uri(SECRET, "ruth@fernlea.example", "room.fernlea.example")
    assert uri.startswith("otpauth://totp/")
    assert "issuer=Snug%20%28room.fernlea.example%29" in uri
    assert f"secret={SECRET}" in uri


def test_terminal_qr_and_grouping() -> None:
    assert len(totp.terminal_qr("otpauth://totp/x?secret=ABC").splitlines()) > 10
    assert totp.grouped("ABCDEFGHIJKL") == "ABCD EFGH IJKL"


def test_window_is_one_step_each_way() -> None:
    # Guard against the window being widened by accident.
    assert totp.WINDOW == 1
    assert timedelta(seconds=totp.STEP_SECONDS) == timedelta(seconds=30)
