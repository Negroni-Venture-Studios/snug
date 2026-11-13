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
from snug.core import doctor, geoip, ratelimit
from snug.core.models import SiteSettings, SystemCheck
from snug.documents import storage
from snug.office import previews
from snug.viewer.models import VerificationCode, ViewerSession


class Command(BaseCommand):
    help = (
        "Delete expired codes, sessions, setup links, rate-limit counts and old reading data, "
        "then refresh the stored checks and DB-IP's country data."
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
        # Pages stamped on other days: each carries its day's date.
        stamped = storage.clear_stamped(keep=timezone.localdate())
        # Recorded only when everything above worked: `snug doctor` and the
        # Overview warn when this falls more than three hours behind.
        SystemCheck.objects.update_or_create(
            name="housekeeping",
            defaults={"ok": True, "message": "", "checked_at": timezone.now()},
        )
        # The checks too slow for the Overview to run on each visit.
        doctor.refresh(timezone.now())
        # DB-IP's country data, for UK-only links: fetched once a month.
        try:
            fetched = geoip.refresh(geoip._now())
        except Exception as error:  # noqa: BLE001 - never let it stop housekeeping
            fetched = f"failed ({type(error).__name__})"
        self.stdout.write(f"GeoIP: {fetched}.")
        # Office previews left waiting, after a restart or once the converter is
        # installed: one step each hour, and a thread to keep it moving.
        try:
            previews.pump()
            previews.drive_in_background()
        except Exception as error:  # noqa: BLE001 - never let it stop housekeeping
            self.stdout.write(f"Office previews: failed ({type(error).__name__}).")
        self.stdout.write(
            f"Housekeeping: reading data: {visits} visit{'s' if visits != 1 else ''}; "
            f"codes: {codes}; viewer sessions: {viewer_sessions}; setup links: {setups}; "
            f"rate-limit windows: {windows}; days of stamped pages: {stamped}."
        )
