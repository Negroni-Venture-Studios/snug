"""The migration that allows one acceptance of each NDA version on a link.

Found in the audit of 10 November 2026: two acceptances posted at once could
both be recorded. A live database may hold such pairs, so the migration keeps
the earliest of each before it adds the constraint.
"""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from snug.links.models import Link
from snug.nda.models import NdaAcceptance, NdaVersion
from snug.rooms.models import Room

pytestmark = pytest.mark.django_db(transaction=True)

BEFORE = [("nda", "0001_initial")]


@pytest.fixture
def before_the_constraint() -> Iterator[MigrationExecutor]:
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    executor.migrate(BEFORE)
    yield MigrationExecutor(connection)
    MigrationExecutor(connection).migrate(latest)


def test_duplicates_are_reduced_to_the_earliest(before_the_constraint: MigrationExecutor) -> None:
    room = Room.objects.create(name="Seed round")
    link = Link.objects.create(token_hash="a" * 64, ref="AB12CD34", email="p@n.example", room=room)
    other = Link.objects.create(token_hash="b" * 64, ref="EF56GH78", email="p@n.example", room=room)
    version = NdaVersion.objects.create(number=1, text="Confidential.", sha256="0" * 64)
    times = {"Later": 2, "Earliest": 1, "Latest": 3}
    for name in times:
        NdaAcceptance.objects.create(link=link, version=version, name=name, email="p@n.example")
    NdaAcceptance.objects.create(link=other, version=version, name="Only", email="p@n.example")
    for name, day in times.items():
        when = datetime(2026, 11, day, tzinfo=UTC)
        NdaAcceptance.objects.filter(name=name).update(accepted_at=when)

    before_the_constraint.loader.build_graph()
    before_the_constraint.migrate([("nda", "0002_one_acceptance_per_link")])

    assert sorted(NdaAcceptance.objects.values_list("name", flat=True)) == ["Earliest", "Only"]
