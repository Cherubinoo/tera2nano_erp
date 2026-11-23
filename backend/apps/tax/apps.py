from django.apps import AppConfig


class TaxConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.tax"

    def ready(self):
        from apps.attachments import registry
        from apps.company.services import audit

        # Money in tax records is never personal data, so nothing is redacted; the changes are kept in the company history.
        for name, label in (("tax_filing", "Tax filing"), ("tax_payment", "Tax payment"), ("tax_credit", "TDS credit"),
                            ("tax_worksheet", "Income tax worksheet"), ("tax_itc_flag", "ITC eligibility")):
            audit.register_entity(name, audit.EntityAudit(label, "tax", bump_version=False))
        registry.register("tax_filing", view="tax.view", manage="tax.manage", label="Tax filing")
        registry.register("tax_payment", view="tax.view", manage="tax.manage", label="Tax payment")
