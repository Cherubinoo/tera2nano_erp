from django.apps import AppConfig


class ComplianceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.compliance"

    def ready(self):
        from apps.attachments import registry
        from apps.company.services import audit

        spec = audit.EntityAudit
        for entity, label in (("compliance_obligation", "Compliance obligation"), ("compliance_item", "Compliance filing"),
                              ("legal_event", "Legal event"), ("contract", "Contract"), ("compliance_policy", "Policy")):
            audit.register_entity(entity, spec(label, "compliance", bump_version=False))
        audit.register_entity("legal_case", spec("Legal case or notice", "compliance", redact=frozenset({"lawyer"}), bump_version=False))

        for entity, label in (("compliance_filing", "Compliance filing"), ("legal_event", "Legal event"), ("contract", "Contract"),
                              ("legal_case", "Legal case or notice"), ("compliance_policy", "Policy")):
            registry.register(entity, view="compliance.view", manage="compliance.manage", label=label)
