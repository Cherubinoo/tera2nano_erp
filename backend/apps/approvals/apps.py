from django.apps import AppConfig


class ApprovalsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.approvals"

    def ready(self):
        from apps.company.services import audit

        audit.register_entity("approval_request", audit.EntityAudit("Approval request", "approvals", bump_version=False))
        audit.register_entity("approval_rule", audit.EntityAudit("Approval rule", "approvals", bump_version=False))
