from django.apps import AppConfig


class AttachmentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.attachments"

    def ready(self):
        from apps.company.services import audit

        audit.register_entity("attachment", audit.EntityAudit("Attachment", "documents", bump_version=False))
