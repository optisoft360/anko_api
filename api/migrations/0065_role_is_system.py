from django.db import migrations, models

SYSTEM_ROLE_NAMES = ("admin", "client")
COMPANY_FULL_ROLES = ("enterprise.owner", "enterprise.admin")
OWNER_DESCRIPTION = "Enterprise owner. Full access to all enterprise routes of their company. No platform admin area."
OLD_OWNER_DESCRIPTION = "Enterprise owner. Full access to all enterprise routes + platform admin area."


def mark_system_roles(apps, schema_editor):
    Role = apps.get_model("api", "Role")
    Permission = apps.get_model("api", "Permission")
    Role.objects.filter(name__in=SYSTEM_ROLE_NAMES).update(is_system=True)
    Role.objects.filter(name__startswith="enterprise.").update(is_system=True)

    # Company owner/admin carried the platform admin routes (dashboard.admin.*). They were never
    # reachable (company roles only resolve enterprise.* keys) but misdescribed the role.
    admin_area = Permission.objects.filter(resource__key__startswith="dashboard.admin.")
    for role in Role.objects.filter(name__in=COMPANY_FULL_ROLES):
        role.permissions.remove(*admin_area)
    Role.objects.filter(name="enterprise.owner", description=OLD_OWNER_DESCRIPTION).update(description=OWNER_DESCRIPTION)


def unmark_system_roles(apps, schema_editor):
    Role = apps.get_model("api", "Role")
    Permission = apps.get_model("api", "Permission")
    admin_area = Permission.objects.filter(resource__key__startswith="dashboard.admin.")
    for role in Role.objects.filter(name__in=COMPANY_FULL_ROLES):
        role.permissions.add(*admin_area)
    Role.objects.filter(name="enterprise.owner", description=OWNER_DESCRIPTION).update(description=OLD_OWNER_DESCRIPTION)


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0064_knowledge_gap_detection"),
    ]

    operations = [
        migrations.AddField(
            model_name="role",
            name="is_system",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(mark_system_roles, unmark_system_roles),
    ]
