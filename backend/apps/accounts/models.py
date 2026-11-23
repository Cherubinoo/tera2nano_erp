import uuid

from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models

from . import access


class Role:
    """Role values (see `access.ROLES`). `FINANCE` and `STAFF` are the names the first version used."""

    ADMIN = access.ADMIN
    MANAGING_PARTNER = access.MANAGING_PARTNER
    PARTNER = access.PARTNER
    FINANCE_MANAGER = access.FINANCE_MANAGER
    HR_MANAGER = access.HR_MANAGER
    PROJECT_MANAGER = access.PROJECT_MANAGER
    EMPLOYEE = access.EMPLOYEE
    FINANCE = FINANCE_MANAGER
    STAFF = EMPLOYEE
    CHOICES = access.ROLES


class ERPUserManager(UserManager):
    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("role", Role.ADMIN)
        return super().create_superuser(username, email, password, **extra_fields)


class User(AbstractUser):
    """ERP user. UUID primary key; `role` decides which permissions (see `access`) the user holds."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    role = models.CharField(max_length=20, choices=Role.CHOICES, default=Role.EMPLOYEE)

    objects = ERPUserManager()

    def codes(self):
        """The set of permission codes this user holds. Recomputed when the matrix is saved."""
        if not self.is_active:
            return set()
        key = (access.current_version(), self.role, self.is_superuser)  # the set is recomputed when any of these change
        cached = getattr(self, "_code_cache", None)
        if cached is None or cached[0] != key:
            held = set(access.CODES) if (self.is_superuser or self.role == Role.ADMIN) else access.codes_for_role(self.role)
            cached = self._code_cache = (key, held)
        return cached[1]

    def has_code(self, code):
        return code in self.codes()

    @property
    def is_company_admin(self):
        """Holds `company.manage`: may change company details (administrators and, by default, managing partners)."""
        return self.has_code("company.manage")

    @property
    def is_finance(self):
        """Holds `finance.manage`: may change bank details and issue invoices and credit notes."""
        return self.has_code("finance.manage")


class RolePermission(models.Model):
    """A saved decision on whether `role` holds `code`. A pair with no row uses the default in `access`."""

    role = models.CharField(max_length=20, choices=Role.CHOICES)
    code = models.CharField(max_length=60)
    granted = models.BooleanField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["role", "code"], name="one_decision_per_role_and_code")]
        ordering = ["role", "code"]

    def __str__(self):
        return f"{self.role} {'may' if self.granted else 'may not'} {self.code}"


class LoginEvent(models.Model):
    """One sign-in attempt or sign-out. Kept as a history; never edited."""

    class Outcome(models.TextChoices):
        SUCCESS = "success", "Signed in"
        FAILED = "failed", "Wrong username or password"
        INACTIVE = "inactive", "Account switched off"
        LOGOUT = "logout", "Signed out"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="login_events")
    # The name typed, kept only when it belongs to a real user: a mistyped password pasted into the name box must not be stored.
    username = models.CharField(max_length=150, blank=True)
    outcome = models.CharField(max_length=10, choices=Outcome.choices)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [models.Index(fields=["-occurred_at"]), models.Index(fields=["user", "-occurred_at"])]

    def __str__(self):
        return f"{self.outcome} {self.username or '(unknown user)'} at {self.occurred_at:%Y-%m-%d %H:%M}"
