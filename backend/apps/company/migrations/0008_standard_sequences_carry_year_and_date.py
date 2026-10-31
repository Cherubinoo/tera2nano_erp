from django.db import migrations


def upgrade_untouched(apps, schema_editor):
    """Standard sequences that have never issued a number now carry the 2-digit year and the date.

    Sequences that already issued numbers, or that an administrator changed, are left exactly as they are.
    """
    Sequence = apps.get_model("company", "DocumentNumberSequence")
    Issued = apps.get_model("company", "IssuedReferenceNumber")
    used = set(Issued.objects.values_list("sequence_id", flat=True))
    for seq in Sequence.objects.filter(letterhead_template__isnull=True, include_short_year=False, date_format=""):
        if seq.pk in used:
            continue
        seq.include_financial_year = False
        seq.include_short_year = True
        seq.date_format = "ddmm"
        seq.save(update_fields=["include_financial_year", "include_short_year", "date_format"])


class Migration(migrations.Migration):
    dependencies = [("company", "0007_template_sequences_restart_yearly")]
    operations = [migrations.RunPython(upgrade_untouched, migrations.RunPython.noop)]
