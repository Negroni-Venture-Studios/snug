"""`snug createadmin` and `snug reset-2fa`, driven through a scripted terminal."""

import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from io import StringIO

import pyotp
import pytest
from django.contrib.auth.hashers import check_password
from django.core.management import CommandError, call_command

from snug.accounts import recovery, totp
from snug.accounts.models import Admin, RecoveryCode
from snug.audit.models import AuditEvent

pytestmark = pytest.mark.django_db

SECRET = totp.new_secret()
PASSWORD = "correct horse battery staple"
EMAIL = "ruth@fernlea.example"


class Terminal:
    """Scripted answers for input() and getpass(), and a record of the prompts."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.answers: list[str] = []
        self.prompts: list[str] = []
        monkeypatch.setattr("builtins.input", self.reply)
        monkeypatch.setattr("getpass.getpass", self.reply)
        monkeypatch.setattr(totp, "new_secret", lambda: SECRET)

    def reply(self, prompt: str = "") -> str:
        self.prompts.append(prompt)
        if not self.answers:
            raise AssertionError(f"Unexpected prompt: {prompt!r}")
        return self.answers.pop(0)

    def script(self, *answers: str | Callable[[], str]) -> None:
        # Callables are resolved lazily, so a TOTP code is generated when asked.
        self.answers = [a() if callable(a) else a for a in answers]


@pytest.fixture
def terminal(monkeypatch: pytest.MonkeyPatch) -> Iterator[Terminal]:
    yield Terminal(monkeypatch)


CODE_STEPS: list[int] = []


def live_code() -> str:
    """A code from the app as it is typed, remembering which 30-second step it is from."""
    totp = pyotp.TOTP(SECRET)
    now = datetime.fromtimestamp(time.time(), tz=UTC)
    CODE_STEPS.append(totp.timecode(now))
    return totp.at(now)


def run(command: str, *args: str) -> str:
    out = StringIO()
    call_command(command, *args, stdout=out)
    return out.getvalue()


def create(terminal: Terminal) -> str:
    terminal.script(PASSWORD, PASSWORD, live_code)
    return run("createadmin", "--email", "Ruth@Fernlea.example")


def test_createadmin_creates_an_active_admin(terminal: Terminal) -> None:
    output = create(terminal)

    admin = Admin.objects.get()
    assert admin.email == EMAIL
    assert admin.is_active
    assert admin.totp_secret == SECRET
    assert admin.totp_confirmed_at is not None
    # The confirming code's own step, not the moment the account was saved:
    # the argon2 hash between them can cross a 30-second boundary.
    assert admin.totp_last_step == CODE_STEPS[-1]
    assert admin.password.startswith("argon2$argon2id$")
    assert check_password(PASSWORD, admin.password)
    assert PASSWORD not in output
    assert f"Admin {EMAIL} created." in output


def test_createadmin_shows_qr_key_and_recovery_codes_once(terminal: Terminal) -> None:
    output = create(terminal)
    assert totp.grouped(SECRET) in output
    assert "█" in output or "▀" in output  # block characters of the QR code
    codes = [line.strip() for line in output.splitlines() if line.startswith("    ")]
    assert len(codes) == 10
    admin = Admin.objects.get()
    assert recovery.remaining(admin) == 10
    assert recovery.use(admin, codes[0])


def test_createadmin_is_audited_without_secrets(terminal: Terminal) -> None:
    create(terminal)
    event = AuditEvent.objects.get()
    assert (event.action, event.actor_type, event.target) == ("admin.created", "cli", EMAIL)
    assert event.details == {}


def test_the_confirming_code_cannot_be_used_again(terminal: Terminal) -> None:
    create(terminal)
    admin = Admin.objects.get()
    code = pyotp.TOTP(SECRET).generate_otp(admin.totp_last_step)
    assert totp.verify(SECRET, code, last_step=admin.totp_last_step) is None


def test_nothing_is_saved_without_a_working_code(terminal: Terminal) -> None:
    terminal.script(PASSWORD, PASSWORD, "000000", "111111", "222222")
    with pytest.raises(CommandError, match="nothing has been saved"):
        run("createadmin", "--email", EMAIL)
    assert not Admin.objects.exists()
    assert not RecoveryCode.objects.exists()
    assert not AuditEvent.objects.exists()


def test_a_wrong_code_can_be_retried(terminal: Terminal) -> None:
    terminal.script(PASSWORD, PASSWORD, "000000", live_code)
    output = run("createadmin", "--email", EMAIL)
    assert "That code is not right" in output
    assert Admin.objects.filter(email=EMAIL, is_active=True).exists()


def test_weak_and_mismatched_passwords_are_refused_then_retried(terminal: Terminal) -> None:
    terminal.script(
        "password1234", "password1234", PASSWORD, "different", PASSWORD, PASSWORD, live_code
    )
    output = run("createadmin", "--email", EMAIL)
    assert "too common" in output
    assert "do not match" in output
    assert check_password(PASSWORD, Admin.objects.get().password)


def test_three_bad_passwords_save_nothing(terminal: Terminal) -> None:
    terminal.script("short", "short", "short", "short", "short", "short")
    with pytest.raises(CommandError, match="No acceptable password"):
        run("createadmin", "--email", EMAIL)
    assert not Admin.objects.exists()


def test_email_can_be_asked_for(terminal: Terminal) -> None:
    terminal.script(EMAIL, PASSWORD, PASSWORD, live_code)
    run("createadmin")
    assert terminal.prompts[0] == "Email address: "
    assert Admin.objects.filter(email=EMAIL).exists()


@pytest.mark.parametrize("email", ["not-an-email", "ruth@", "@fernlea.example", ""])
def test_invalid_email_addresses_are_refused(terminal: Terminal, email: str) -> None:
    with pytest.raises(CommandError, match="not a valid email address"):
        run("createadmin", "--email", email or " ")


def test_duplicate_admins_are_refused(terminal: Terminal) -> None:
    create(terminal)
    with pytest.raises(CommandError, match="already exists"):
        run("createadmin", "--email", "RUTH@fernlea.example")


def test_reset_2fa_replaces_the_secret_and_codes(
    terminal: Terminal, monkeypatch: pytest.MonkeyPatch
) -> None:
    create(terminal)
    admin = Admin.objects.get()
    old_code = recovery.issue(admin)[0]

    # The fixture pins new_secret; switch it so the reset gets a different one.
    replacement = pyotp.random_base32(length=32)
    monkeypatch.setattr(totp, "new_secret", lambda: replacement)
    terminal.script("y", lambda: pyotp.TOTP(replacement).now())
    output = run("reset-2fa", EMAIL)

    admin.refresh_from_db()
    assert admin.totp_secret == replacement
    assert not recovery.use(admin, old_code)
    assert recovery.remaining(admin) == 10
    assert "New authenticator and recovery codes set" in output
    assert AuditEvent.objects.filter(action="admin.2fa_reset", target=EMAIL).count() == 1


def test_reset_2fa_needs_confirmation(terminal: Terminal) -> None:
    create(terminal)
    terminal.script("n")
    output = run("reset-2fa", EMAIL)
    assert "Nothing has been changed." in output
    assert Admin.objects.get().totp_secret == SECRET
    assert not AuditEvent.objects.filter(action="admin.2fa_reset").exists()


def test_reset_2fa_without_a_working_code_changes_nothing(terminal: Terminal) -> None:
    create(terminal)
    terminal.script("yes", "000000", "111111", "222222")
    with pytest.raises(CommandError, match="nothing has been saved"):
        run("reset-2fa", EMAIL)
    assert Admin.objects.get().totp_secret == SECRET


def test_reset_2fa_for_an_unknown_admin(terminal: Terminal) -> None:
    with pytest.raises(CommandError, match="no admin with the address"):
        run("reset-2fa", "nobody@fernlea.example")


def test_passwords_are_hashed_with_argon2id_only() -> None:
    from django.conf import settings

    assert settings.PASSWORD_HASHERS == ["django.contrib.auth.hashers.Argon2PasswordHasher"]
