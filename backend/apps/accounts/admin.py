from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import LoginEvent, RolePermission, User


@admin.register(LoginEvent)
class LoginEventAdmin(admin.ModelAdmin):
    list_display = ("occurred_at", "username", "outcome", "ip_address")
    list_filter = ("outcome",)
    readonly_fields = [f.name for f in LoginEvent._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(RolePermission)
class RolePermissionAdmin(admin.ModelAdmin):
    list_display = ("role", "code", "granted")
    list_filter = ("role",)


@admin.register(User)
class ERPUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("ERP", {"fields": ("role",)}),)
    list_display = ("username", "email", "role", "is_active", "is_staff")
