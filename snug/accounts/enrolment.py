"""Authenticator enrolment for the command line: show a QR code, confirm a code."""

from collections.abc import Callable

from snug.accounts import totp

ATTEMPTS = 3


class EnrolmentFailedError(Exception):
    """The admin did not confirm a working code. Nothing has been saved."""


def enrol(
    *,
    email: str,
    domain: str,
    ask: Callable[[str], str],
    say: Callable[[str], None],
) -> tuple[str, int]:
    """Show a new secret, then insist on one working code from it.

    Returns the secret and the time step of the confirming code, which becomes
    the admin's last used step, so that code cannot be used again to sign in.
    """
    secret = totp.new_secret()
    say(
        "\nScan this QR code with an authenticator app, such as 1Password, "
        "Google Authenticator or Authy:\n"
    )
    say(totp.terminal_qr(totp.provisioning_uri(secret, email, domain)))
    say(f"Or enter this key by hand: {totp.grouped(secret)}\n")

    for attempt in range(1, ATTEMPTS + 1):
        step = totp.verify(secret, ask("Six-digit code from the app: "), last_step=0)
        if step is not None:
            return secret, step
        if attempt < ATTEMPTS:
            say(
                "That code is not right. Check the app's clock is set automatically, "
                "then try again."
            )
    raise EnrolmentFailedError(
        "No working code was entered, so nothing has been saved. Run the command again."
    )


def recovery_codes_message(email: str, codes: list[str]) -> str:
    lines = "\n".join(f"    {code}" for code in codes)
    return (
        f"\nRecovery codes for {email}\n\n{lines}\n\n"
        "Each code works once, in place of a code from the app. Store them somewhere safe, "
        "such as a password manager. They will not be shown again.\n"
    )
