"""
Standardize usernames: login remains case-insensitive and usernames are unique case-insensitively.

Hand-written to avoid unrelated schema churn.
"""
from django.db import migrations, models
import django.db.models.functions.text


def assert_no_case_variant_usernames(apps, schema_editor):
    User = apps.get_model("api", "User")
    duplicates = list(
        User.objects
        .annotate(username_ci=django.db.models.functions.text.Lower("username"))
        .values("username_ci")
        .annotate(row_count=models.Count("id"))
        .filter(row_count__gt=1)
        .order_by("username_ci")
        .values_list("username_ci", flat=True)[:10]
    )
    if duplicates:
        sample = ", ".join(duplicates)
        raise RuntimeError(
            "Cannot add case-insensitive username uniqueness: existing duplicates found "
            f"(sample: {sample}). Merge or rename them first."
        )


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0062_ks_topic_unique_title"),
    ]

    operations = [
        migrations.RunPython(assert_no_case_variant_usernames, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.UniqueConstraint(
                django.db.models.functions.text.Lower("username"),
                name="uniq_user_username_ci",
            ),
        ),
    ]
