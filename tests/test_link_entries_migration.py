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
