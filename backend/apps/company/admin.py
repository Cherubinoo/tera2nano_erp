from django.contrib import admin

from .models import CompanyProfile, CompanyProfileAuditEvent, IssuedDocument, IssuedReferenceNumber


class ReadOnlyAdmin(admin.ModelAdmin):
    """Company data is edited through the API (which audits every change); the admin site is for inspection."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CompanyProfileAuditEvent)
class AuditEventAdmin(ReadOnlyAdmin):
    list_display = ("occurred_at", "action", "entity_type", "category", "actor_label", "request_id")
    list_filter = ("entity_type", "category", "action")
    exclude = ("previous_values", "new_values")  # values are visible through the audited API only


@admin.register(IssuedReferenceNumber)
class ReferenceNumberAdmin(ReadOnlyAdmin):
    list_display = ("reference_number", "document_type", "status", "allocated_at")
    list_filter = ("document_type", "status")


@admin.register(IssuedDocument)
class IssuedDocumentAdmin(ReadOnlyAdmin):
    list_display = ("reference_number", "document_type", "issue_date", "status", "generated_at")
    exclude = ("snapshot",)


@admin.register(CompanyProfile)
class CompanyProfileAdmin(ReadOnlyAdmin):
    list_display = ("display_name", "legal_name", "status", "setup_status", "version")
