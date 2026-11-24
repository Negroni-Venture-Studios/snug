"""The migration that gives every link its list of entries (issue #174).

Each existing link is for its one address, so that address becomes its only
entry, and a link already opened keeps its first open as that person's, so
they are not emailed about again.
"""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from snug.rooms.models import Room

pytestmark = pytest.mark.django_db(transaction=True)

BEFORE = [("links", "0008_neutral_until")]
AFTER = [("links", "0009_link_entries_and_people")]


@pytest.fixture
def before_entries() -> Iterator[MigrationExecutor]:
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    executor.migrate(BEFORE)
    yield MigrationExecutor(connection)
    MigrationExecutor(connection).migrate(latest)


def test_each_link_is_for_its_one_address(before_entries: MigrationExecutor) -> None:
    old = before_entries.loader.project_state(BEFORE).apps
    room = Room.objects.create(name="Seed round")  # rooms are not part of this change
    links = old.get_model("links", "Link")
    opened = datetime(2026, 11, 20, 9, 30, tzinfo=UTC)
    links.objects.create(
        token_hash="a" * 64,
        ref="AB12",
        email="Priya@Northfield.example",
        room_id=room.pk,
        first_opened_at=opened,
    )
    links.objects.create(
        token_hash="b" * 64, ref="CD34", email="sam@eastgate.example", room_id=room.pk
    )

    before_entries.loader.build_graph()
    before_entries.migrate(AFTER)
    new = MigrationExecutor(connection).loader.project_state(AFTER).apps
    migrated = {link.ref: link for link in new.get_model("links", "Link").objects.all()}
    assert migrated["AB12"].entries == "priya@northfield.example"
    assert migrated["CD34"].entries == "sam@eastgate.example"
    people = list(
        new.get_model("links", "LinkPerson").objects.values_list("email", "first_opened_at")
    )
    assert people == [("priya@northfield.example", opened)]


# Security audit of 1.4.0, F14: str.split() also splits on a vertical tab, a
# form feed, and U+001C to U+001F, which Django's validate_email accepts in a
# quoted local part, so an old address could become an entry for a domain.
# A link whose old address is not a plain one now admits nobody.
@pytest.mark.parametrize(
    "old",
    [
        '"a\x0bfund.example\x0bb"@evil.example',
        '"a\x0cfund.example\x0cb"@evil.example',
        '"a\x1cfund.example\x1cb"@evil.example',
        "\u017fam@fund.example",  # a long s, which casefold turns into another mailbox
        '"quoted"@fund.example',
    ],
)
def test_an_old_address_that_is_not_plain_admits_nobody(
    before_entries: MigrationExecutor, old: str
) -> None:
    apps = before_entries.loader.project_state(BEFORE).apps
    room = Room.objects.create(name="Seed round")
    opened = datetime(2026, 11, 20, 9, 30, tzinfo=UTC)
    apps.get_model("links", "Link").objects.create(
        token_hash="c" * 64, ref="EF56", email=old, room_id=room.pk, first_opened_at=opened
    )

    before_entries.loader.build_graph()
    before_entries.migrate(AFTER)
    new = MigrationExecutor(connection).loader.project_state(AFTER).apps
    link = new.get_model("links", "Link").objects.get()
    assert link.entries == "" and link.email == ""
    assert not new.get_model("links", "LinkPerson").objects.exists()

    from snug.links.models import Link

    assert not Link.objects.get().admits("x@fund.example")


def test_a_plain_old_address_keeps_its_email(before_entries: MigrationExecutor) -> None:
    apps = before_entries.loader.project_state(BEFORE).apps
    room = Room.objects.create(name="Seed round")
    apps.get_model("links", "Link").objects.create(
        token_hash="d" * 64, ref="GH78", email="priya@northfield.example", room_id=room.pk
    )
    before_entries.loader.build_graph()
    before_entries.migrate(AFTER)
    new = MigrationExecutor(connection).loader.project_state(AFTER).apps
    link = new.get_model("links", "Link").objects.get()
    assert link.entries == link.email == "priya@northfield.example"
