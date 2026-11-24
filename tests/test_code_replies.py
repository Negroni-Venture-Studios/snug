"""What a posted code is answered with (security audit of 1.4.0, F10 and F11).

Anyone with a link's URL can post a code with any address. No reply to that
post may depend on whether the link admits the address, or on whether the
address has a live code: either would let a holder test guesses at who the
link is for, or see that a colleague is signing in, without the wrong-address
limit or an email to anyone. Only the page that send_code returns, after it
has sent a code, names the address.
"""

import re
from datetime import timedelta

import pytest
from django.core import mail as django_mail
from django.test import Client
from django.utils import timezone

from snug.core import ratelimit
from snug.documents.models import Document
from snug.links import service
from snug.links.models import Link
from snug.viewer import codes, limits
from snug.viewer.models import VerificationCode, ViewerSession
from tests.test_domain_links import PRIYA, STRANGER, TOMAS, code_sent_to, for_firm, post

pytestmark = pytest.mark.django_db

_CSRF = re.compile(r'name="csrfmiddlewaretoken" value="[^"]*"')


@pytest.fixture
def document() -> Document:
    return Document.objects.create(title="Seed deck")


def for_one(document: Document) -> service.Issued:
    return for_firm(document, entries=[PRIYA])


def reply(token: str, email: str, ip: str = "198.51.100.7") -> tuple[int, str]:
    """The status and page for a wrong code posted with this address, the address masked."""
    response = post(Client(REMOTE_ADDR=ip), f"/v/{token}/code", {"email": email, "code": "000000"})
    page = _CSRF.sub("", response.content.decode().split("<main", 1)[-1])
    return response.status_code, page.replace(email, "TYPED")


def neutral(link: Link) -> None:
    Link.objects.filter(pk=link.pk).update(neutral_until=timezone.now() + timedelta(minutes=10))


def checks_spent(ip: str) -> None:
    for _ in range(limits.CHECK_PER_IP.limit):
        ratelimit.hit(limits.CHECK_PER_IP, ip)


def same(*replies: tuple[int, str]) -> bool:
    return all(each == replies[0] for each in replies)


# ---------------------------------------------------------------- F10: who the link admits


def test_a_link_for_one_address_answers_a_right_and_a_wrong_guess_alike(
    document: Document,
) -> None:
    issued = for_one(document)
    right, wrong = reply(issued.token, PRIYA), reply(issued.token, STRANGER)
    assert right == wrong
    assert right[0] == 400
    assert "your email address" in right[1]


def test_a_link_for_a_domain_answers_its_staff_and_a_stranger_alike(document: Document) -> None:
    issued = for_firm(document)
    assert same(
        reply(issued.token, PRIYA),
        reply(issued.token, "anyone@northfield.example"),
        reply(issued.token, STRANGER),
    )


def test_neutral_mode_answers_alike(document: Document) -> None:
    issued = for_firm(document)
    neutral(issued.link)
    assert reply(issued.token, PRIYA) == reply(issued.token, STRANGER)


@pytest.mark.parametrize("in_neutral_mode", [False, True])
def test_past_the_limit_on_checks_the_reply_is_the_same(
    document: Document, in_neutral_mode: bool
) -> None:
    issued = for_firm(document)
    if in_neutral_mode:
        neutral(issued.link)
    checks_spent("198.51.100.8")
    right = reply(issued.token, PRIYA, ip="198.51.100.8")
    wrong = reply(issued.token, STRANGER, ip="198.51.100.8")
    assert right == wrong
    assert right[0] == 429
    assert ("If this link was sent to that address" in right[1]) == in_neutral_mode


@pytest.mark.usefixtures("email_on")
def test_after_a_wrong_code_the_typed_address_comes_back_and_the_right_code_works(
    document: Document,
) -> None:
    issued = for_firm(document)
    client = Client(REMOTE_ADDR="198.51.100.9")
    post(client, f"/v/{issued.token}/code/send", {"email": PRIYA})
    code = code_sent_to(PRIYA)
    wrong = f"{(int(code) + 1) % 1_000_000:06d}"
    page = post(client, f"/v/{issued.token}/code", {"email": PRIYA, "code": wrong})
    html = page.content.decode()
    assert PRIYA not in html.split("<form", 1)[0]  # not named above the forms
    # Both forms carry back what was typed: the next try, and a new code.
    assert html.count(f'name="email" value="{PRIYA}"') == 2
    again = post(client, f"/v/{issued.token}/code/send", {"email": PRIYA})
    assert again.status_code == 200 and len(django_mail.outbox) == 2
    signed_in = post(
        client, f"/v/{issued.token}/code", {"email": PRIYA, "code": code_sent_to(PRIYA)}
    )
    assert signed_in.status_code == 303
    assert ViewerSession.objects.filter(email=PRIYA).exists()


# ---------------------------------------------------------------- F11: whose code is live


def test_a_colleagues_live_code_is_not_shown_by_a_wrong_code(document: Document) -> None:
    issued = for_firm(document)
    codes.issue(issued.link, TOMAS)
    tomas, priya = reply(issued.token, TOMAS), reply(issued.token, PRIYA)
    assert tomas == priya
    assert "That code is not right, or it has expired." in tomas[1]
    assert "tries left" not in tomas[1]


def test_the_daily_cap_answers_every_address_alike(document: Document) -> None:
    issued = for_firm(document)
    codes.issue(issued.link, TOMAS)
    for _ in range(codes.CHECKS_PER_LINK_PER_DAY.limit):
        ratelimit.hit(codes.CHECKS_PER_LINK_PER_DAY, str(issued.link.pk))
    tomas, priya = reply(issued.token, TOMAS), reply(issued.token, PRIYA)
    assert tomas == priya
    assert tomas[0] == 429
    assert "Too many attempts on this link today." in tomas[1]
    # Checked before the code is looked for, so nothing is counted against it.
    assert VerificationCode.objects.get(email=TOMAS).failures == 0
