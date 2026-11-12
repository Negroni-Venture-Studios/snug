from django.db import models


class RateLimitWindow(models.Model):
    """How many times one key was hit in one fixed time window.

    Keys are SHA-256 hashes, so the table never holds an email address or an
    IP address in clear.
    """

    key = models.CharField(max_length=64)
    window_start = models.BigIntegerField()
    count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = (
            models.UniqueConstraint(fields=("key", "window_start"), name="ratelimit_key_window"),
        )

    def __str__(self) -> str:
        return f"{self.key[:12]} @ {self.window_start}: {self.count}"


class SiteSettings(models.Model):
    """Instance-wide settings an admin changes in the interface. One row, always pk 1."""

    # A link to the founder's own privacy policy, shown to viewers (UK GDPR:
    # the founder is the controller of viewers' data). Only plain https URLs.
    privacy_url = models.URLField(max_length=500, blank=True)
    # How long reading data (visits, time per page) is kept after its link is
    # revoked or expires. 12 months by default; some founders need 7 years.
    reading_retention_months = models.PositiveSmallIntegerField(default=12)
    # The founder's accent colour for investors' pages, as #rrggbb, or "" for
    # Snug's default. snug.core.brand makes it readable wherever it is used.
    accent_colour = models.CharField(max_length=7, blank=True)
    # The founder's logo, as re-encoded by Snug (snug.core.logo), or "" for none.
    logo_name = models.CharField(max_length=40, blank=True)
    # The company's name, as investors know it, or "" to use SNUG_COMPANY_NAME.
    company_name = models.CharField(max_length=100, blank=True)
    # Whether the name shows beside the logo. Off for a logo that spells it out.
    show_name_with_logo = models.BooleanField(default=True)
    # The tab icon, as re-encoded by Snug (snug.core.logo), or "" to choose one.
    icon_name = models.CharField(max_length=40, blank=True)
    # Whether Word, PowerPoint and Excel files are shown in the browser, from
    # their previews, when the LibreOffice converter is installed.
    office_previews = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "site settings"

    def __str__(self) -> str:
        return "Site settings"

    @classmethod
    def load(cls) -> "SiteSettings":
        row, _ = cls.objects.get_or_create(pk=1)
        return row


def company_name() -> str:
    """The company's name: as saved in Settings, or else SNUG_COMPANY_NAME, or ""."""
    from django.conf import settings

    saved = SiteSettings.load().company_name
    return saved or str(settings.CONFIG.company_name)


class SystemCheck(models.Model):
    """The latest result of one of `snug doctor`'s checks, one row per check.

    Housekeeping records each success here as "housekeeping", so a run that
    stops succeeding shows up as late.
    """

    name = models.CharField(max_length=40, unique=True)
    ok = models.BooleanField()
    message = models.CharField(max_length=300, blank=True)
    checked_at = models.DateTimeField()

    def __str__(self) -> str:
        return f"{self.name}: {'ok' if self.ok else 'problem'}"


class EmailSettings(models.Model):
    """How Snug sends email, as saved in Settings. One row, always pk 1.

    Apart from SiteSettings, which every viewer page reads, because these
    hold passwords. A blank provider means nothing is saved, and Snug uses
    the SMTP settings in snug.env (snug.core.mail.outgoing).
    """

    provider = models.CharField(max_length=10, blank=True)  # "", "smtp" or "google"
    from_address = models.EmailField(blank=True)
    smtp_host = models.CharField(max_length=253, blank=True)
    smtp_port = models.PositiveIntegerField(null=True, blank=True)
    smtp_username = models.CharField(max_length=254, blank=True)
    # Never shown again once saved; Snug needs it as it is to sign in.
    smtp_password = models.CharField(max_length=500, blank=True)
    smtp_security = models.CharField(max_length=10, blank=True)
    # Google Workspace (snug.core.gmail): the founder's own internal OAuth client,
    # and the refresh token the sending mailbox's approval gave.
    google_client_id = models.CharField(max_length=200, blank=True)
    google_client_secret = models.CharField(max_length=200, blank=True)
    google_refresh_token = models.CharField(max_length=1000, blank=True)
    google_account = models.EmailField(blank=True)
    # Another of the mailbox's addresses to send as, such as one on an alias
    # domain; "" for the mailbox's own. Gmail sends it only if the mailbox has
    # it under Send mail as, and otherwise uses the mailbox's own address.
    google_from_address = models.EmailField(blank=True)
    google_connected_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "email settings"

    def __str__(self) -> str:
        return "Email settings"

    def __repr__(self) -> str:
        # Never let the password reach a log or a traceback.
        return f"EmailSettings(provider={self.provider!r}, smtp_host={self.smtp_host!r})"

    @classmethod
    def load(cls) -> "EmailSettings":
        row, _ = cls.objects.get_or_create(pk=1)
        return row
