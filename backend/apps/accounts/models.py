import uuid

from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models


class Role:
    ADMIN = "admin"
    FINANCE = "finance"
    STAFF = "staff"
    CHOICES = [
        (ADMIN, "Company administrator"),
        (FINANCE, "Finance"),
        (STAFF, "Staff"),
    ]


class ERPUserManager(UserManager):
    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("role", Role.ADMIN)
        return super().create_superuser(username, email, password, **extra_fields)


class User(AbstractUser):
    """ERP user. UUID primary key; `role` drives company-module permissions."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    role = models.CharField(max_length=20, choices=Role.CHOICES, default=Role.STAFF)

    objects = ERPUserManager()

    @property
    def is_company_admin(self):
        return self.is_active and (self.role == Role.ADMIN or self.is_superuser)

    @property
    def is_finance(self):
        return self.is_active and (self.role == Role.FINANCE or self.is_company_admin)
