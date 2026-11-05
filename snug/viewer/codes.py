"""Six-digit codes, emailed to prove a viewer can read the link's inbox.

A code is single use, lasts ten minutes and survives at most five wrong
attempts. Each link has at most one live code: asking for another replaces it.
The code is kept only as an HMAC over the link's current token hash, so
reissuing the link also ends its code.
"""

import re
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from snug.links.models import Link
from snug.viewer.models import VerificationCode

CODE_MINUTES = 10
MAX_FAILURES = 5
_CODE = re.compile(r"[0-9]{6}")
# People paste codes with spaces, or type them in two groups of three.
_SEPARATORS = re.compile(r"[\s-]")


@dataclass(frozen=True)
class Result:
    ok: bool
    tries_left: int  # 0 once the code is used up, expired or missing


def _digest(link: Link, code: str) -> str:
    return salted_hmac(
        "snug.viewer.code", f"{link.pk}:{link.token_hash}:{code}", algorithm="sha256"
    ).hexdigest()


def issue(link: Link) -> str:
    """A new code for this link, replacing any other. Returns it, once."""
    code = f"{secrets.randbelow(1_000_000):06d}"
    with transaction.atomic():
        VerificationCode.objects.filter(link=link).delete()
        VerificationCode.objects.create(
            link=link,
            code_hash=_digest(link, code),
            expires_at=timezone.now() + timedelta(minutes=CODE_MINUTES),
        )
    return code


def check(link: Link, typed: str) -> Result:
    """Try a code. A correct code is used up; a wrong one counts against it."""
    live = VerificationCode.objects.filter(
        link=link, expires_at__gt=timezone.now(), failures__lt=MAX_FAILURES
    ).first()
    if live is None:
        return Result(ok=False, tries_left=0)

    code = _SEPARATORS.sub("", typed)
    if _CODE.fullmatch(code) and constant_time_compare(live.code_hash, _digest(link, code)):
        # Deleting is the claim: of two requests with the same code, only one
        # deletes the row, so a code can never be used twice.
        claimed, _ = VerificationCode.objects.filter(pk=live.pk).delete()
        return Result(ok=claimed == 1, tries_left=0)

    with transaction.atomic():
        VerificationCode.objects.filter(pk=live.pk).update(failures=F("failures") + 1)
        failures = VerificationCode.objects.filter(pk=live.pk).values_list("failures", flat=True)
        used = failures.first() or MAX_FAILURES
        if used >= MAX_FAILURES:
            VerificationCode.objects.filter(pk=live.pk).delete()
    return Result(ok=False, tries_left=max(MAX_FAILURES - used, 0))
