"""Recovery codes: issued once, stored hashed, spent once, owner only."""

import re

import pytest

from snug.accounts import recovery
from snug.accounts.models import Admin, RecoveryCode

pytestmark = pytest.mark.django_db
FORMAT = re.compile(r"^[a-z2-7]{4}(-[a-z2-7]{4}){3}$")


@pytest.fixture
def admin() -> Admin:
    return Admin.objects.create(email="ruth@fernlea.example", is_active=True)


def test_ten_distinct_codes_in_the_expected_format(admin: Admin) -> None:
    codes = recovery.issue(admin)
    assert len(codes) == 10 == len(set(codes))
    assert all(FORMAT.match(c) for c in codes)


def test_only_hashes_are_stored(admin: Admin) -> None:
    codes = recovery.issue(admin)
    stored = list(RecoveryCode.objects.values_list("code_hash", flat=True))
    assert all(len(h) == 64 for h in stored)
    for code in codes:
        assert code not in stored
        assert code.replace("-", "") not in stored


def test_each_code_works_once(admin: Admin) -> None:
    code = recovery.issue(admin)[0]
    assert recovery.use(admin, code)
    assert not recovery.use(admin, code)
    assert recovery.remaining(admin) == 9


def test_case_spaces_and_hyphens_do_not_matter(admin: Admin) -> None:
    code = recovery.issue(admin)[3]
    assert recovery.use(admin, " " + code.upper().replace("-", " ") + " ")


def test_another_admins_code_does_not_work(admin: Admin) -> None:
    other = Admin.objects.create(email="pat@fernlea.example", is_active=True)
    code = recovery.issue(other)[0]
    recovery.issue(admin)
    assert not recovery.use(admin, code)
    assert recovery.use(other, code)


@pytest.mark.parametrize(
    "guess", ["", "aaaa", "aaaa-aaaa-aaaa-aaaa", "not a code at all, honestly"]
)
def test_wrong_codes_fail(admin: Admin, guess: str) -> None:
    recovery.issue(admin)
    assert not recovery.use(admin, guess)


def test_reissuing_replaces_the_old_codes(admin: Admin) -> None:
    old = recovery.issue(admin)
    new = recovery.issue(admin)
    assert not set(old) & set(new)
    assert not recovery.use(admin, old[0])
    assert recovery.use(admin, new[0])
    assert RecoveryCode.objects.filter(admin=admin).count() == 10
