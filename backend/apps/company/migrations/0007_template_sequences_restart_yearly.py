from django.db import migrations


def restart_yearly(apps, schema_editor):
    """Letterhead-template counters now restart at 00001 every year (the 2-digit year is in the number)."""
    Sequence = apps.get_model("company", "DocumentNumberSequence")
    Sequence.objects.filter(letterhead_template__isnull=False, reset_frequency="never").update(
        reset_frequency="yearly"
    )


def back_to_never(apps, schema_editor):
    Sequence = apps.get_model("company", "DocumentNumberSequence")
    Sequence.objects.filter(letterhead_template__isnull=False, reset_frequency="yearly").update(
        reset_frequency="never"
    )


class Migration(migrations.Migration):
    dependencies = [("company", "0006_companybranding_body_top_gap_mm_and_more")]
    operations = [migrations.RunPython(restart_yearly, back_to_never)]
