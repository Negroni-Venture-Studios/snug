"""Each person on a link accepts the NDA for themselves (issue #174)."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("nda", "0002_one_acceptance_per_link"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="ndaacceptance",
            name="nda_one_acceptance_per_link",
        ),
        migrations.AddConstraint(
            model_name="ndaacceptance",
            constraint=models.UniqueConstraint(
                fields=("link", "version", "email"), name="nda_one_acceptance_per_person"
            ),
        ),
    ]
