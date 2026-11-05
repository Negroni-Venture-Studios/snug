"""Configuration: every rule, and every problem reported at once."""

import secrets
from pathlib import Path

import pytest

from snug.config import DEFAULT_DATA_DIR, ConfigError, load_config, read_env_file

KEY = secrets.token_urlsafe(64)
GOOD = {"SNUG_SECRET_KEY": KEY, "SNUG_DOMAIN": "room.example.com"}


def problems(env: dict[str, str]) -> list[str]:
    with pytest.raises(ConfigError) as caught:
        load_config(env)
    return caught.value.problems


def test_minimal_configuration_has_safe_defaults() -> None:
    config = load_config(GOOD)
    assert config.domain == "room.example.com"
    assert config.data_dir == Path(DEFAULT_DATA_DIR)
    assert config.https is True
    assert config.debug is False
    assert config.origin == "https://room.example.com"
    assert config.database_path == Path(DEFAULT_DATA_DIR) / "snug.sqlite3"


def test_every_problem_is_reported_at_once() -> None:
    found = problems({"SNUG_DATA_DIR": "relative/path", "SNUG_HTTPS": "maybe"})
    assert len(found) == 4
    joined = " ".join(found)
    for name in ("SNUG_SECRET_KEY", "SNUG_DOMAIN", "SNUG_DATA_DIR", "SNUG_HTTPS"):
        assert name in joined


def test_error_message_lists_each_problem() -> None:
    with pytest.raises(ConfigError) as caught:
        load_config({})
    message = str(caught.value)
    assert message.startswith("Snug cannot start.")
    assert message.count("\n  - ") == 2


@pytest.mark.parametrize(
    "key",
    ["short", "a" * 80, "ab" * 40, KEY[:49]],
    ids=["short", "one-character", "two-characters", "49-characters"],
)
def test_weak_secret_keys_are_refused(key: str) -> None:
    found = problems({**GOOD, "SNUG_SECRET_KEY": key})
    assert found == [found[0]]
    assert "SNUG_SECRET_KEY is too weak" in found[0]


@pytest.mark.parametrize(
    "domain",
    [
        "https://room.example.com",
        "room.example.com:8443",
        "room.example.com/path",
        "room",
        "-room.example.com",
        "room.example.com.",
        "room_1.example.com",
        "192.168.1.10",
    ],
)
def test_domains_must_be_bare_host_names(domain: str) -> None:
    found = problems({**GOOD, "SNUG_DOMAIN": domain})
    assert len(found) == 1
    assert "SNUG_DOMAIN must be a bare host name" in found[0]


def test_domain_is_lowercased() -> None:
    assert load_config({**GOOD, "SNUG_DOMAIN": "Room.Example.COM"}).domain == "room.example.com"


def test_data_dir_must_be_absolute() -> None:
    found = problems({**GOOD, "SNUG_DATA_DIR": "data"})
    assert found == ["SNUG_DATA_DIR must be an absolute path, not 'data'."]


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_true_flags(value: str) -> None:
    env = {**GOOD, "SNUG_DOMAIN": "localhost", "SNUG_DEBUG": value}
    assert load_config(env).debug is True


@pytest.mark.parametrize("value", ["0", "false", "No", "off", ""])
def test_false_flags(value: str) -> None:
    assert load_config({**GOOD, "SNUG_HTTPS": "1", "SNUG_DEBUG": value}).debug is False


def test_plain_http_is_refused_on_a_real_domain() -> None:
    found = problems({**GOOD, "SNUG_HTTPS": "false"})
    assert found == ["SNUG_HTTPS can only be false when SNUG_DOMAIN is localhost."]


def test_debug_is_refused_on_a_real_domain() -> None:
    found = problems({**GOOD, "SNUG_DEBUG": "true"})
    assert found == ["SNUG_DEBUG can only be true when SNUG_DOMAIN is localhost."]


def test_localhost_may_use_plain_http_and_debug() -> None:
    config = load_config(
        {**GOOD, "SNUG_DOMAIN": "localhost", "SNUG_HTTPS": "false", "SNUG_DEBUG": "true"}
    )
    assert config.origin == "http://localhost"
    assert config.debug is True


def test_repr_never_shows_the_secret_key() -> None:
    assert KEY not in repr(load_config(GOOD))


def test_env_file_parsing(tmp_path: Path) -> None:
    path = tmp_path / "snug.env"
    path.write_text(
        "# Snug settings\n"
        "\n"
        "SNUG_DOMAIN=room.example.com\n"
        'SNUG_SECRET_KEY="quoted value"\n'
        "export SNUG_DATA_DIR='/srv/snug'\n"
        "SNUG_EMPTY=\n",
        encoding="utf-8",
    )
    assert read_env_file(path) == {
        "SNUG_DOMAIN": "room.example.com",
        "SNUG_SECRET_KEY": "quoted value",
        "SNUG_DATA_DIR": "/srv/snug",
        "SNUG_EMPTY": "",
    }


def test_env_file_errors_never_echo_the_line(tmp_path: Path) -> None:
    path = tmp_path / "snug.env"
    path.write_text("SNUG_DOMAIN=room.example.com\nnot-a-setting hunter2\n", encoding="utf-8")
    with pytest.raises(ConfigError) as caught:
        read_env_file(path)
    assert caught.value.problems == [f"{path}, line 2: expected KEY=value."]
    assert "hunter2" not in str(caught.value)


def test_trusted_proxies_default_to_the_loopback_addresses() -> None:
    assert [str(n) for n in load_config(GOOD).trusted_proxies] == ["127.0.0.1/32", "::1/128"]


def test_trusted_proxies_can_be_set() -> None:
    config = load_config({**GOOD, "SNUG_TRUSTED_PROXIES": "172.30.0.0/24, 10.0.0.5"})
    assert [str(n) for n in config.trusted_proxies] == ["172.30.0.0/24", "10.0.0.5/32"]


@pytest.mark.parametrize("value", ["0.0.0.0/0", "::/0", "not-an-address", "172.30.0.0/24, bogus"])
def test_unsafe_or_invalid_trusted_proxies_are_refused(value: str) -> None:
    found = problems({**GOOD, "SNUG_TRUSTED_PROXIES": value})
    assert len(found) == 1
    assert "SNUG_TRUSTED_PROXIES" in found[0]


def test_email_is_off_by_default_on_a_real_domain() -> None:
    config = load_config(GOOD)
    assert not config.email_enabled and not config.email_outbox


def test_localhost_without_smtp_writes_to_an_outbox() -> None:
    config = load_config({**GOOD, "SNUG_DOMAIN": "localhost"})
    assert config.email_outbox and config.email_enabled


def test_smtp_settings() -> None:
    config = load_config(
        {
            **GOOD,
            "SNUG_SMTP_HOST": "smtp.postmarkapp.com",
            "SNUG_SMTP_USERNAME": "key",
            "SNUG_SMTP_PASSWORD": "a-secret-password",
            "SNUG_EMAIL_FROM": "rooms@fernlea.example",
            "SNUG_COMPANY_NAME": "  Fernlea   Robotics ",
        }
    )
    assert config.email_enabled and not config.email_outbox
    assert (config.smtp_port, config.smtp_security) == (587, "starttls")
    assert config.company_name == "Fernlea Robotics"
    assert "a-secret-password" not in repr(config)


def test_tls_defaults_to_port_465() -> None:
    config = load_config(
        {
            **GOOD,
            "SNUG_SMTP_HOST": "smtp.example.com",
            "SNUG_EMAIL_FROM": "a@b.example",
            "SNUG_SMTP_SECURITY": "tls",
        }
    )
    assert config.smtp_port == 465


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ({"SNUG_SMTP_HOST": "smtp.example.com"}, "SNUG_EMAIL_FROM is not set"),
        ({"SNUG_EMAIL_FROM": "not an address"}, "SNUG_EMAIL_FROM must be an email address"),
        ({"SNUG_SMTP_PORT": "99999"}, "SNUG_SMTP_PORT must be a port number"),
        ({"SNUG_SMTP_SECURITY": "maybe"}, "SNUG_SMTP_SECURITY must be"),
        (
            {
                "SNUG_SMTP_HOST": "smtp.example.com",
                "SNUG_EMAIL_FROM": "a@b.example",
                "SNUG_SMTP_SECURITY": "none",
            },
            "can only be none when SNUG_DOMAIN is localhost",
        ),
        ({"SNUG_COMPANY_NAME": "x" * 101}, "SNUG_COMPANY_NAME must be 100 characters"),
    ],
)
def test_bad_email_settings_are_refused(extra: dict[str, str], message: str) -> None:
    found = problems({**GOOD, **extra})
    assert any(message in p for p in found), found


def test_viewer_sessions_last_seven_days_by_default() -> None:
    assert load_config(GOOD).viewer_session_days == 7
    assert load_config({**GOOD, "SNUG_VIEWER_SESSION_DAYS": "30"}).viewer_session_days == 30


@pytest.mark.parametrize("value", ["0", "91", "a week", "-3", "1.5"])
def test_viewer_session_days_must_be_sensible(value: str) -> None:
    found = problems({**GOOD, "SNUG_VIEWER_SESSION_DAYS": value})
    assert any(
        "SNUG_VIEWER_SESSION_DAYS must be a whole number of days from 1 to 90" in p for p in found
    )
