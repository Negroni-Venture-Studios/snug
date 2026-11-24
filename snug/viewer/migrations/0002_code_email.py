"""A code proves one address on a link (issue #174)."""

from django.db import migrations, models


def email_from_link(apps, schema_editor):  # type: ignore[no-untyped-def]
    VerificationCode = apps.get_model("viewer", "VerificationCode")
    for code in VerificationCode.objects.select_related("link"):
        code.email = code.link.email.strip().casefold()
        code.save(update_fields=["email"])


class Migration(migrations.Migration):
    dependencies = [
        ("viewer", "0001_initial"),
        ("links", "0009_link_entries_and_people"),
    ]

    operations = [
        migrations.AddField(
            model_name="verificationcode",
            name="email",
            field=models.EmailField(default="", max_length=254),
            preserve_default=False,
        ),
        migrations.RunPython(email_from_link, migrations.RunPython.noop),
    ]
