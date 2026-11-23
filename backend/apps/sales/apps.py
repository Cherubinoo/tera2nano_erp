from django.apps import AppConfig


class SalesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.sales"

    def ready(self):
        from apps.company.services import audit

        audit.register_entity("quotation", audit.EntityAudit("Quotation", "sales", bump_version=False))
        audit.register_entity("recurring_invoice", audit.EntityAudit("Recurring invoice", "sales", bump_version=False))
        audit.register_entity("reminder_rule", audit.EntityAudit("Payment reminder rule", "sales", bump_version=False))
        audit.register_entity("payment_reminder", audit.EntityAudit("Payment reminder", "sales", bump_version=False))
        from . import approval_hooks
        approval_hooks.register()
