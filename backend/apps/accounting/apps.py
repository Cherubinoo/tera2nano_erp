from django.apps import AppConfig


class AccountingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounting"

    def ready(self):
        from apps.attachments import registry
        from apps.company.services import audit

        from . import approval_hooks
        approval_hooks.register()
        audit.register_entity("asset_category", audit.EntityAudit("Asset category", "accounting", bump_version=False))
        audit.register_entity("fixed_asset", audit.EntityAudit("Fixed asset", "accounting", bump_version=False))
        audit.register_entity("depreciation_run", audit.EntityAudit("Depreciation run", "accounting", bump_version=False))
        audit.register_entity("asset_disposal", audit.EntityAudit("Asset disposal", "accounting", bump_version=False))
        audit.register_entity("budget", audit.EntityAudit("Budget", "accounting", bump_version=False))
        registry.register("fixed_asset", view="accounting.view", manage="accounting.manage", label="Fixed asset")
