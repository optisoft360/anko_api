import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Admin inbox fields on SupportRequest. Hand-written: only these three additions."""

    dependencies = [
        ("api", "0065_role_is_system"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="supportrequest",
            name="assigned_to",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name="assigned_support_requests", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name="supportrequest",
            name="internal_note",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="supportrequest",
            name="updated_at",
            field=models.DateTimeField(auto_now=True, null=True),
        ),
    ]
