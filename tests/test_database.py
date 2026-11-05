"""SQLite runs in WAL mode, with the pragmas Snug relies on, on every connection."""

import pytest
from django.core.management import call_command
from django.db import connection

pytestmark = pytest.mark.django_db


def pragma(name: str) -> object:
    with connection.cursor() as cursor:
        cursor.execute(f"PRAGMA {name}")
        return cursor.fetchone()[0]


def test_write_ahead_logging() -> None:
    assert pragma("journal_mode") == "wal"


def test_foreign_keys_are_enforced() -> None:
    assert pragma("foreign_keys") == 1


def test_busy_timeout_queues_writers() -> None:
    assert pragma("busy_timeout") == 5000


def test_synchronous_normal() -> None:
    assert pragma("synchronous") == 1


def test_transactions_take_the_write_lock_at_once() -> None:
    assert getattr(connection, "transaction_mode", None) == "IMMEDIATE"


def test_no_model_changes_without_migrations() -> None:
    call_command("makemigrations", "--check", "--dry-run", verbosity=0)
