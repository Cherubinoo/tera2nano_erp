from django.db import migrations

CREATE = """
CREATE FUNCTION company_audit_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'company_companyprofileauditevent is append-only (% blocked)', TG_OP
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER company_audit_no_update_delete
    BEFORE UPDATE OR DELETE ON company_companyprofileauditevent
    FOR EACH ROW EXECUTE FUNCTION company_audit_append_only();
"""

DROP = """
DROP TRIGGER IF EXISTS company_audit_no_update_delete ON company_companyprofileauditevent;
DROP FUNCTION IF EXISTS company_audit_append_only();
"""


class Migration(migrations.Migration):
    dependencies = [("company", "0001_initial")]
    operations = [migrations.RunSQL(CREATE, reverse_sql=DROP)]
