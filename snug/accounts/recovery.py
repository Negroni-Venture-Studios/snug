"""Recovery codes: ten single-use codes, issued at enrolment, shown once."""

import hashlib
import re
import secrets

from django.db import transaction
from django.utils import timezone

from snug.accounts.models import Admin, RecoveryCode

COUNT = 10
# 16 characters of base32: 80 random bits, well beyond guessing or cracking.
_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"
_LENGTH = 16


def _normalise(code: str) -> str:
    return re.sub(r"[\s-]", "", code).lower()


def hash_code(code: str) -> str:
    return hashlib.sha256(_normalise(code).encode("ascii", errors="replace")).hexdigest()


def _new_code() -> str:
    raw = "".join(secrets.choice(_ALPHABET) for _ in range(_LENGTH))
    return "-".join(raw[i : i + 4] for i in range(0, _LENGTH, 4))


@transaction.atomic
def issue(admin: Admin) -> list[str]:
    """Replace an admin's recovery codes. Returns the new codes, in clear, once."""
    RecoveryCode.objects.filter(admin=admin).delete()
    codes = [_new_code() for _ in range(COUNT)]
    RecoveryCode.objects.bulk_create(
        RecoveryCode(admin=admin, code_hash=hash_code(c)) for c in codes
    )
    return codes


@transaction.atomic
def use(admin: Admin, code: str) -> bool:
    """Spend one of this admin's unused codes. True if it was valid."""
    if len(_normalise(code)) != _LENGTH:
        return False
    spent = RecoveryCode.objects.filter(
        admin=admin, code_hash=hash_code(code), used_at__isnull=True
    ).update(used_at=timezone.now())
    return spent == 1


def remaining(admin: Admin) -> int:
    return RecoveryCode.objects.filter(admin=admin, used_at__isnull=True).count()
