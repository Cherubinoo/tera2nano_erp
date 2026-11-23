from django.apps import AppConfig


class PurchasesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.purchases"

    def ready(self):
        from apps.attachments import registry
        from apps.company.services import audit

        spec = audit.EntityAudit
        audit.register_entity("vendor", spec("Vendor", "purchases", bump_version=False,
                                             redact=frozenset({"email", "phone", "gstin", "pan", "bank_account_number",
                                                               "bank_ifsc", "bank_account_holder"})))
        audit.register_entity("purchase_request", spec("Purchase request", "purchases", bump_version=False))
        audit.register_entity("vendor_quotation", spec("Vendor quotation", "purchases", bump_version=False))
        audit.register_entity("purchase_order", spec("Purchase order", "purchases", bump_version=False))
        audit.register_entity("vendor_bill", spec("Vendor bill", "purchases", bump_version=False))
        audit.register_entity("vendor_payment", spec("Vendor payment", "purchases", bump_version=False))
        for entity, label in (("vendor", "Vendor"), ("vendor_bill", "Vendor bill"), ("purchase_order", "Purchase order"),
                              ("vendor_quotation", "Vendor quotation"), ("purchase_request", "Purchase request"),
                              ("vendor_payment", "Vendor payment")):
            registry.register(entity, view="purchases.view", manage="purchases.manage", label=label)
        from . import approval_hooks
        approval_hooks.register()
