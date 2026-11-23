from django.apps import AppConfig


class WorkplaceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.workplace"

    def ready(self):
        from apps.attachments import registry
        from apps.company.services import audit

        audit.register_entity("visitor", audit.EntityAudit("Visitor", "workplace", redact=frozenset({"phone"}), bump_version=False))
        for name, label in (("meeting", "Meeting"), ("task", "Task"), ("announcement", "Announcement"), ("circular", "Circular"),
                            ("asset", "Office asset")):
            audit.register_entity(name, audit.EntityAudit(label, "workplace", bump_version=False))
        # Meeting papers and circular enclosures are visible to anyone who can see workplace records; assets are admin-only.
        registry.register("meeting", view="workplace.view", manage="workplace.manage", label="Meeting")
        registry.register("circular", view="workplace.view", manage="workplace.manage", label="Circular")
        registry.register("asset", view="workplace.manage", manage="workplace.manage", label="Office asset")
