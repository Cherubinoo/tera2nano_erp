from django.apps import AppConfig


class ReportsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.reports"

    def ready(self):
        from apps.company.services import audit

        # CSV exports of sensitive figures (salary) are written to the audit history.
        audit.register_entity("report_export", audit.EntityAudit("Report export", "reports", bump_version=False))
