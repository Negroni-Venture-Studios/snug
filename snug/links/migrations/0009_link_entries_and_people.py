"""Links list who may open them: addresses and domains (issue #174)."""

from django.conf import settings
from django.db import migrations, models
from django.db.models.deletion import CASCADE, SET_NULL


def entries_from_email(apps, schema_editor):  # type: ignore[no-untyped-def]
    """Each existing link is for its one address. Its first open becomes that person's."""
    Link = apps.get_model("links", "Link")
    LinkPerson = apps.get_model("links", "LinkPerson")
    for link in Link.objects.all():
        address = link.email.strip().casefold()
        link.entries = address
        link.save(update_fields=["entries"])
        if link.first_opened_at is not None:
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
