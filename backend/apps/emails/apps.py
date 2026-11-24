from django.apps import AppConfig


class EmailsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.emails"
    verbose_name = "Email"

    def ready(self):
        from apps.company import daily
        from apps.company.services import audit

        audit.register_entity("email_settings", audit.EntityAudit("Email settings", "email", bump_version=False))
        from . import jobs

        daily.register("Payment reminder emails", jobs.send_due_reminders)
