from django.db import migrations, models


class Migration(migrations.Migration):
    """Adds the business_pilot / business choices to Plan.tier. Choices only: no SQL, no data change."""

    dependencies = [
        ("api", "0066_support_inbox_fields"),
    ]

    operations = [
        migrations.AlterField(
            model_name="plan",
            name="tier",
            field=models.CharField(
                choices=[
                    ("free", "Free"),
                    ("premium", "Premium"),
                    ("ultra", "Ultra"),
                    ("business_pilot", "Ankard Business Pilot"),
                    ("business", "Ankard Business"),
                ],
                max_length=20,
                unique=True,
            ),
        ),
    ]
