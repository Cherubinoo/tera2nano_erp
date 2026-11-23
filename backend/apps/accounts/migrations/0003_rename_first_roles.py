from django.db import migrations

# The first version had three roles; they carry over to the matching new roles with the same powers.
RENAMES = {"finance": "finance_manager", "staff": "employee"}


def forwards(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    for old, new in RENAMES.items():
        User.objects.filter(role=old).update(role=new)


def backwards(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    for old, new in RENAMES.items():
        User.objects.filter(role=new).update(role=old)
    # Roles that did not exist before fall back to the closest old one.
    User.objects.filter(role__in=["managing_partner"]).update(role="admin")
    User.objects.filter(role__in=["partner", "hr_manager", "project_manager"]).update(role="staff")


class Migration(migrations.Migration):
    dependencies = [("accounts", "0002_roles_permissions_login_history")]
    operations = [migrations.RunPython(forwards, backwards)]
