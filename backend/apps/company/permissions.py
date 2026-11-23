from rest_framework.permissions import SAFE_METHODS, BasePermission

from .constants import AccessClass


class IsCompanyAdminOrReadOnly(BasePermission):
    """Any signed-in user may read; only company administrators may change."""

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if request.method in SAFE_METHODS:
            return True
        return user.is_company_admin


class IsFinanceOrReadOnly(BasePermission):
    """Any signed-in user may read (masked data); finance and administrators may change."""

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if request.method in SAFE_METHODS:
            return True
        return user.is_finance


class IsCompanyAdmin(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_company_admin)


class IsFinance(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_finance)


def visible_classifications(user):
    """Document classes the user may see, from the permission matrix (see apps.accounts.access)."""
    classes = [AccessClass.INTERNAL]
    if user.has_code("documents.view_confidential"):
        classes.append(AccessClass.CONFIDENTIAL)
    if user.has_code("documents.view_restricted"):
        classes.append(AccessClass.RESTRICTED)
    return classes
