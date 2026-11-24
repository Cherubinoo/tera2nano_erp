from django.contrib import admin

from .models import EmailMessage, EmailSettings


@admin.register(EmailMessage)
class EmailMessageAdmin(admin.ModelAdmin):
    list_display = ("created_at", "subject", "status", "kind", "sent_by")
    list_filter = ("status", "kind")
    search_fields = ("subject", "object_label")
    readonly_fields = [f.name for f in EmailMessage._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


admin.site.register(EmailSettings)
