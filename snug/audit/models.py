"""The audit log: an append-only record of admin actions and viewer access.

Rows can be added and never changed or removed. The model refuses updates
and deletes, and database triggers (in the migration) refuse them too, so
not even raw SQL from inside Snug can rewrite history.
"""

from typing import Any

from django.db import models


class AppendOnlyError(Exception):
    """Raised on any attempt to change or remove an audit event."""


class AuditEventQuerySet(models.QuerySet["AuditEvent"]):
    def update(self, **kwargs: Any) -> int:
        raise AppendOnlyError("Audit events cannot be changed.")

    def delete(self) -> tuple[int, dict[str, int]]:
        raise AppendOnlyError("Audit events cannot be deleted.")


class AuditEvent(models.Model):
    class ActorType(models.TextChoices):
        ADMIN = "admin", "Admin"
        VIEWER = "viewer", "Viewer"
        CLI = "cli", "Command line"
        SYSTEM = "system", "System"

    at = models.DateTimeField(auto_now_add=True, db_index=True)
    actor_type = models.CharField(max_length=10, choices=ActorType.choices)
    # The actor's email address, kept as text so the record survives the account.
    actor = models.CharField(max_length=254, blank=True)
    action = models.CharField(max_length=64, db_index=True)
    target = models.CharField(max_length=254, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True)
    country = models.CharField(max_length=2, blank=True)
    details = models.JSONField(default=dict, blank=True)

    objects = AuditEventQuerySet.as_manager()

    class Meta:
        ordering = ("-at", "-id")

    def __str__(self) -> str:
        return f"{self.at:%Y-%m-%d %H:%M} {self.action} {self.target}".strip()

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.pk is not None and not self._state.adding:
            raise AppendOnlyError("Audit events cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise AppendOnlyError("Audit events cannot be deleted.")


def record(
    action: str,
    *,
    actor_type: str,
    actor: str = "",
    target: str = "",
    ip_address: str | None = None,
    user_agent: str = "",
    country: str = "",
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    """Add one event to the audit log. Never pass secrets in details."""
    return AuditEvent.objects.create(
        action=action,
        actor_type=actor_type,
        actor=actor,
        target=target,
        ip_address=ip_address,
        user_agent=user_agent[:512],
        country=country[:2],
        details=details or {},
    )
