from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class ERPUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("ERP", {"fields": ("role",)}),)
    list_display = ("username", "email", "role", "is_active", "is_staff")
