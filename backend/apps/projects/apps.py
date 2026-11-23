from django.apps import AppConfig


class ProjectsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.projects"

    def ready(self):
        from apps.attachments import registry
        from apps.company.services import audit

        # One audited record type: every change to a project, its milestones, tasks, team, budget and time is written
        # against the project, so the Activity tab is a single history.
        audit.register_entity("project", audit.EntityAudit("Project", "projects", bump_version=False))
        # Agreements and other files, with an expiry date.
        registry.register("project", view="projects.view", manage="projects.manage", label="Project")
        from . import budget_approval
        budget_approval.register()
