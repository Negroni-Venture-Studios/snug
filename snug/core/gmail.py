"""Sending through Google Workspace, with the Gmail API over HTTPS.

Google's mail servers accept only ports 25, 465 and 587, which DigitalOcean
blocks, so Snug uses the Gmail API on port 443 instead. The founder makes an
internal OAuth client in their own Google Cloud project; the sending mailbox
approves it once, for the gmail.send permission alone, and Snug keeps the
refresh token that approval gives. Access tokens last an hour and are kept in
memory only.

Standard library only. Tokens, codes and secrets are never logged: errors
carry Google's error name or the HTTP status, nothing more.
"""

import base64
import hashlib
import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core.mail import EmailMessage

if TYPE_CHECKING:
    from snug.core.mail import Outgoing

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - an address, not a secret
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
# Send only: Snug can never read the mailbox. openid and email name the
# account that approved, so Settings can say which mailbox sends.
SCOPE = "openid email https://www.googleapis.com/auth/gmail.send"
CALLBACK_PATH = "/admin/settings/email/google/callback"
CLIENT_SUFFIX = ".apps.googleusercontent.com"
TIMEOUT = 15


class GoogleError(OSError):
    """Google refused, or could not be reached. The message names the reason only."""


def callback_url() -> str:
    """Where Google sends the admin back: pasted into the OAuth client as a redirect URI."""
    return f"{settings.CONFIG.origin}{CALLBACK_PATH}"


def _request(url: str, *, form: Any = None, body: Any = None, token: str = "") -> Any:
    """POST a form or JSON to Google and return its JSON answer."""
    headers = {"User-Agent": "Snug (self-hosted)"}
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    else:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 - Google's fixed https URLs
            raw = response.read(1024 * 1024)
    except urllib.error.HTTPError as error:
        reason = f"HTTP {error.code}"
        try:
            detail = json.loads(error.read(64 * 1024) or b"{}")
            name = detail.get("error")
            if isinstance(name, dict):  # the Gmail API's shape
                name = name.get("status")
            if isinstance(name, str) and name.replace("_", "").isalnum():
                reason = name
        except (ValueError, OSError):
            pass
        raise GoogleError(reason) from None
    except (urllib.error.URLError, TimeoutError) as error:
        raise GoogleError(type(error).__name__) from None
    return json.loads(raw or b"{}")


def new_verifier() -> tuple[str, str]:
    """A PKCE code verifier, and its S256 challenge."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode().rstrip("=")


def auth_url(client_id: str, state: str, challenge: str) -> str:
    query = {
        "client_id": client_id,
        "redirect_uri": callback_url(),
        "response_type": "code",
        "scope": SCOPE,
        # A refresh token, asked for afresh each time, so reconnecting always gives one.
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(query)}"


def _account(id_token: str) -> str:
    """The approving account's address, from the ID token's claims.

    The token came straight from Google's token endpoint over TLS, which is
    the case where Google says its signature need not be checked.
    """
    try:
        payload = id_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        return ""
    email = claims.get("email", "")
    return email if isinstance(email, str) and claims.get("email_verified") else ""


def exchange(code: str, verifier: str, client_id: str, client_secret: str) -> tuple[str, str]:
    """Trade the code Google sent back for a refresh token and the account's address."""
    answer = _request(
        TOKEN_URL,
        form={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": callback_url(),
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        },
    )
    return str(answer.get("refresh_token", "")), _account(str(answer.get("id_token", "")))


def revoke(refresh_token: str) -> None:
    _request(REVOKE_URL, form={"token": refresh_token})


# Access tokens, by a hash of the refresh token they came from: (token, expiry).
_tokens: dict[str, tuple[str, float]] = {}
_lock = threading.Lock()


def forget_tokens() -> None:
    with _lock:
        _tokens.clear()


def _access_token(now: "Outgoing") -> str:
    key = hashlib.sha256(now.refresh_token.encode()).hexdigest()
    with _lock:
        held = _tokens.get(key)
        if held and held[1] > time.time():
            return held[0]
    answer = _request(
        TOKEN_URL,
        form={
            "client_id": now.client_id,
            "client_secret": now.client_secret,
            "refresh_token": now.refresh_token,
            "grant_type": "refresh_token",
        },
    )
    token = str(answer.get("access_token", ""))
    if not token:
        raise GoogleError("no_access_token")
    lasts = float(answer.get("expires_in", 3600))
    with _lock:
        _tokens[key] = (token, time.time() + lasts - 60)
    return token


def send(message: EmailMessage, now: "Outgoing") -> None:
    """Send one message through the Gmail API, as the connected mailbox."""
    raw = base64.urlsafe_b64encode(message.message().as_bytes()).decode()
    _request(SEND_URL, body={"raw": raw}, token=_access_token(now))
