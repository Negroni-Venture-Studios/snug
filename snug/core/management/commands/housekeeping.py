"""`snug housekeeping`: clear what has expired, and reading data past its time.

`snug serve` runs this every hour. It reports counts only, never who, and
records when it last succeeded for `snug doctor`.
"""

from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from snug.accounts.models import AdminSetup
from snug.analytics import retention
from snug.audit.models import AuditEvent, record
from snug.core import ratelimit
from snug.core.models import SiteSettings, SystemCheck
from snug.viewer.models import VerificationCode, ViewerSession


class Command(BaseCommand):
    help = (
        "Delete expired codes, sessions, setup links and rate-limit counts, and old reading data."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        now = timezone.now()
        visits = retention.purge(now=now)
        if visits:
            record(
                "system.reading_data_deleted",
                actor_type=AuditEvent.ActorType.SYSTEM,
                details={"visits": visits, "months": SiteSettings.load().reading_retention_months},
            )
        codes, _ = VerificationCode.objects.filter(expires_at__lte=now).delete()
        viewer_sessions, _ = ViewerSession.objects.filter(expires_at__lte=now).delete()
        setups, _ = AdminSetup.objects.filter(expires_at__lte=now).delete()
        windows = ratelimit.purge_expired()
        # Recorded only when everything above worked: `snug doctor` and the
        # Overview warn when this falls more than three hours behind.
        SystemCheck.objects.update_or_create(
            name="housekeeping",
            defaults={"ok": True, "message": "", "checked_at": timezone.now()},
        )
        self.stdout.write(
            f"Housekeeping: reading data: {visits} visit{'s' if visits != 1 else ''}; "
            f"codes: {codes}; viewer sessions: {viewer_sessions}; setup links: {setups}; "
            f"rate-limit windows: {windows}."
        )
