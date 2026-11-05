"""The audit log is append-only, in the ORM and in the database itself."""

import pytest
from django.db import IntegrityError, connection, transaction

from snug.audit.models import AppendOnlyError, AuditEvent, record

pytestmark = pytest.mark.django_db


@pytest.fixture
def event() -> AuditEvent:
    return record(
        "admin.created", actor_type=AuditEvent.ActorType.CLI, target="ruth@fernlea.example"
    )


def test_events_are_recorded(event: AuditEvent) -> None:
    stored = AuditEvent.objects.get()
    assert (stored.action, stored.actor_type, stored.target) == (
        "admin.created",
        "cli",
        "ruth@fernlea.example",
    )
    assert stored.at is not None


def test_model_refuses_changes(event: AuditEvent) -> None:
    event.target = "someone-else@example.com"
    with pytest.raises(AppendOnlyError):
        event.save()


def test_model_refuses_deletion(event: AuditEvent) -> None:
    with pytest.raises(AppendOnlyError):
        event.delete()


def test_querysets_refuse_bulk_changes(event: AuditEvent) -> None:
    with pytest.raises(AppendOnlyError):
        AuditEvent.objects.all().update(target="x")
    with pytest.raises(AppendOnlyError):
        AuditEvent.objects.all().delete()


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE audit_auditevent SET target = 'rewritten'",
        "DELETE FROM audit_auditevent",
    ],
)
def test_database_refuses_raw_changes(event: AuditEvent, sql: str) -> None:
    with (
        pytest.raises(IntegrityError, match="append-only"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(sql)
    assert AuditEvent.objects.get().target == "ruth@fernlea.example"


def test_long_user_agents_are_truncated() -> None:
    stored = record("viewer.opened", actor_type="viewer", user_agent="x" * 2000)
    assert len(stored.user_agent) == 512
