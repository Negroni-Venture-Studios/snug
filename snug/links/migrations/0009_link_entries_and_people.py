"""Links list who may open them: addresses and domains (issue #174)."""

import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import migrations, models
from django.db.models.deletion import CASCADE, SET_NULL

# A copy of snug.links.models.plain_address as it was for 1.4.0, so this
# migration keeps its meaning if that function changes.
_NOT_PLAIN = frozenset(' \t\r\n"(),:;<>[]\\')
_DOMAIN_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")


def _plain(email: str) -> str:
    address = email.strip().casefold()
    if len(address) > 254 or address.count("@") != 1 or not _NOT_PLAIN.isdisjoint(address):
        return ""
    local, _, domain = address.partition("@")
    labels = domain.split(".")
    if not local or ".." in address or len(labels) < 2:
        return ""
    if not all(len(label) <= 63 and _DOMAIN_LABEL.fullmatch(label) for label in labels):
        return ""
    try:
        validate_email(address)
    except ValidationError:
        return ""
    return address


def entries_from_email(apps, schema_editor):  # type: ignore[no-untyped-def]
    """Each existing link is for its one address. Its first open becomes that person's.

    Only a plain address, unchanged by folding its case, becomes an entry
    (security audit of 1.4.0, F14). Entries are split on whitespace, which
    a quoted local part can hold, so another address could become an entry
    for a domain; and casefold turns some letters, such as a long s, into
    another mailbox. Such a link is left admitting nobody, and needs making
    again.
    """
    Link = apps.get_model("links", "Link")
    LinkPerson = apps.get_model("links", "LinkPerson")
    for link in Link.objects.all():
        stored = link.email.strip().lower()  # as 1.2.0 compared it
        address = stored if stored and _plain(stored) == stored else ""
        link.entries = address
        link.email = address
        link.save(update_fields=["entries", "email"])
        if address and link.first_opened_at is not None:
            LinkPerson.objects.create(
                link=link, email=address, first_opened_at=link.first_opened_at
            )


class Migration(migrations.Migration):
    dependencies = [
        ("links", "0008_neutral_until"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="link",
            name="entries",
            field=models.TextField(default=""),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="link",
            name="email",
            field=models.EmailField(blank=True, max_length=254),
        ),
        migrations.CreateModel(
            name="LinkPerson",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("email", models.EmailField(max_length=254)),
                ("first_opened_at", models.DateTimeField(blank=True, null=True)),
                ("blocked_at", models.DateTimeField(blank=True, null=True)),
                (
                    "blocked_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "link",
                    models.ForeignKey(on_delete=CASCADE, related_name="people", to="links.link"),
                ),
            ],
            options={
                "ordering": ("email",),
                "constraints": [
                    models.UniqueConstraint(fields=("link", "email"), name="link_person_once")
                ],
            },
        ),
        migrations.RunPython(entries_from_email, migrations.RunPython.noop),
    ]
