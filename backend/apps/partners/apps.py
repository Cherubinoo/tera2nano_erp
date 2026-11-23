from django.apps import AppConfig


class PartnersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.partners"

    def ready(self):
        from apps.attachments import registry
        from apps.company.services import audit

        for name, label in (("partner_transaction", "Partner transaction"), ("partner_profit_share", "Profit-sharing ratio"),
                            ("partner_allocation", "Profit allocation"), ("partner_account", "Partner capital account"),
                            ("partner_settings", "Partner settings")):
            audit.register_entity(name, audit.EntityAudit(label, "partners", bump_version=False))
        registry.register("partner", view="partners.view", manage="partners.manage", label="Partner")
        try:  # the approvals module may not exist yet
            from . import approvals_hook
            approvals_hook.register()
        except ImportError:
            pass
