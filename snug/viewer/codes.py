"""Six-digit codes, emailed to prove a viewer can read the link's inbox.

A code is single use, lasts ten minutes and survives at most five wrong
attempts. Each link has at most one live code: asking for another replaces it,
with five fresh tries, so the checks for each link are also capped per day.
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

from snug.core import ratelimit
from snug.links.models import Link
from snug.viewer.models import VerificationCode

CODE_MINUTES = 10
MAX_FAILURES = 5
# With five tries per code, this allows ten codes' worth of guesses a day: a
# one-in-20,000 chance of guessing right, and each new code emails the owner.
# Only checks against a live code count, so using up the day takes about ten
# codes, each emailed to the link's owner and rate limited per link.
CHECKS_PER_LINK_PER_DAY = ratelimit.Limit("viewer.code.check.link", limit=50, window=24 * 60 * 60)
_CODE = re.compile(r"[0-9]{6}")
# People paste codes with spaces, or type them in two groups of three.
_SEPARATORS = re.compile(r"[\s-]")


@dataclass(frozen=True)
class Result:
    ok: bool
    tries_left: int  # 0 once the code is used up, expired or missing
    limited: bool = False  # this link's checks for the day are used up


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
    live = VerificationCode.objects.filter(link=link).first()
    if live is None:
        # Nothing to guess, so nothing is counted: posting codes to a link
        # with no live code must not lock out the person it was sent to.
        return Result(ok=False, tries_left=0)
    if not ratelimit.allow(CHECKS_PER_LINK_PER_DAY, str(link.pk)):
        return Result(ok=False, tries_left=0, limited=True)
    return _attempt(link, live, typed)


def _attempt(link: Link, live: VerificationCode, typed: str) -> Result:
    # Claim a try before comparing. The guarded update counts it only while
    # tries remain and the code is unexpired, so requests racing on the same
    # code cannot make more than five comparisons between them.
    claimed = VerificationCode.objects.filter(
        pk=live.pk, failures__lt=MAX_FAILURES, expires_at__gt=timezone.now()
    ).update(failures=F("failures") + 1)
    if not claimed:
        VerificationCode.objects.filter(pk=live.pk).delete()
        return Result(ok=False, tries_left=0)

    code = _SEPARATORS.sub("", typed)
    if _CODE.fullmatch(code) and constant_time_compare(live.code_hash, _digest(link, code)):
        # Deleting is the use: of two requests with the same code, only one
        # deletes the row, so a code can never be used twice.
        deleted, _ = VerificationCode.objects.filter(pk=live.pk).delete()
        return Result(ok=deleted == 1, tries_left=0)

    used = (
        VerificationCode.objects.filter(pk=live.pk).values_list("failures", flat=True).first()
        or MAX_FAILURES
    )
    if used >= MAX_FAILURES:
        VerificationCode.objects.filter(pk=live.pk).delete()
    return Result(ok=False, tries_left=MAX_FAILURES - used)
