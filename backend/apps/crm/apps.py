from django.apps import AppConfig


class CrmConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.crm"

    def ready(self):
        from apps.attachments import registry
        from apps.company.services import audit

        for name, label, redact in (
            ("lead", "Lead", frozenset({"email", "phone"})),
            ("deal", "Deal", frozenset()),
            ("crm_account", "CRM account", frozenset({"email", "phone"})),
            ("crm_contact", "CRM contact", frozenset({"email", "phone"})),
            ("crm_activity", "CRM activity", frozenset()),
            ("crm_product", "CRM product", frozenset()),
            ("crm_stage", "Pipeline stage", frozenset()),
            ("crm_source", "Lead source", frozenset()),
            ("crm_lost_reason", "Lost reason", frozenset()),
        ):
            audit.register_entity(name, audit.EntityAudit(label, "crm", redact=redact, bump_version=False))
        registry.register("crm_lead", view="crm.view", manage="crm.manage", label="Lead")
        registry.register("crm_deal", view="crm.view", manage="crm.manage", label="Deal")
        registry.register("crm_account", view="crm.view", manage="crm.manage", label="CRM account")
