"""Roles, permission codes and the role-permission matrix.

A permission is a short code such as `company.manage`. The defaults below say which role holds which permission; an
administrator can change any role's permissions in the app (stored as `RolePermission` rows, one explicit row per role and
code once decided). A role/code pair with no row uses the default, so a permission added by a new module takes effect with
its default without any data migration. The `admin` role, the two partner roles and superusers always hold every
permission (see `ALL_ACCESS_ROLES`), so the matrix can never lock a partner or everyone out, and a code added by a later
module reaches them without any change.

Each module adds its own codes to `PERMISSIONS` and `DEFAULT_MATRIX`.
"""
from rest_framework.permissions import BasePermission

ADMIN = "admin"
MANAGING_PARTNER = "managing_partner"
PARTNER = "partner"
FINANCE_MANAGER = "finance_manager"
HR_MANAGER = "hr_manager"
PROJECT_MANAGER = "project_manager"
EMPLOYEE = "employee"

ROLES = [
    (ADMIN, "Admin"),
    (MANAGING_PARTNER, "Managing Partner"),
    (PARTNER, "Partner"),
    (FINANCE_MANAGER, "Finance Manager"),
    (HR_MANAGER, "HR Manager"),
    (PROJECT_MANAGER, "Project Manager"),
    (EMPLOYEE, "Employee"),
]
ROLE_VALUES = [r[0] for r in ROLES]

# (code, what it allows, group shown in the matrix)
PERMISSIONS = [
    ("company.view", "View company details", "Company"),
    ("company.manage", "Change company details: profile, addresses, tax, branding, numbering, policies", "Company"),
    ("finance.manage", "Change bank accounts; issue invoices and credit notes; verify bank details", "Finance"),
    ("documents.issue", "Issue letters, quotations, minutes and operations documents", "Documents"),
    ("documents.view_confidential", "View confidential documents", "Documents"),
    ("documents.view_restricted", "View restricted documents", "Documents"),
    ("sales.view", "View customers, invoices, payments and sales reports", "Sales"),
    ("sales.manage", "Add and edit customers and draft invoices; record payments", "Sales"),
    ("sales.issue", "Issue and void invoices, proforma invoices, credit and debit notes", "Sales"),
    ("accounting.view", "View the books: ledger, journal, trial balance and financial statements", "Accounting"),
    ("accounting.manage", "Record expenses, income and journal entries; reconcile bank accounts; edit the chart of accounts", "Accounting"),
    ("purchases.view", "View vendors, purchase orders, bills and vendor payments", "Purchases"),
    ("purchases.manage", "Create and change vendors, purchase requests, orders, bills and vendor payments", "Purchases"),
    ("tax.view", "View GST registers, returns, TDS and tax records", "Tax"),
    ("tax.manage", "Record GST returns, tax payments, TDS and advance tax", "Tax"),
    ("hr.view", "View the employee directory and departments", "People"),
    ("hr.manage", "Manage employee records, attendance and leave", "People"),
    ("hr.payroll", "See salaries; prepare and run payroll", "People"),
    ("projects.view", "View projects, budgets and milestones", "Projects"),
    ("projects.manage", "Create and change projects, budgets and milestones", "Projects"),
    ("approvals.view", "See every approval request, not only your own", "Approvals"),
    ("approvals.manage", "Set the approval rules", "Approvals"),
    ("compliance.view", "View the compliance calendar and filing records", "Compliance"),
    ("compliance.manage", "Record filings and manage the compliance calendar", "Compliance"),
    ("partners.view", "View partner capital, profit sharing, loans and withdrawals", "Partners"),
    ("partners.manage", "Record partner capital, loans, withdrawals and profit sharing", "Partners"),
    ("workplace.view", "View meetings, tasks, announcements and circulars", "Workplace"),
    ("workplace.manage", "Schedule meetings, assign tasks, publish announcements and circulars", "Workplace"),
    ("crm.view", "View leads, deals and activities", "CRM"),
    ("crm.manage", "Create and change leads, deals and activities", "CRM"),
    ("reports.view", "Open the reports and analytics pages (each report still needs its own module's permission)", "Reports"),
    ("audit.view", "View the company change history", "Administration"),
    ("users.manage", "Manage users, roles and the permission matrix", "Administration"),
    ("users.view_activity", "View login history and user-management activity", "Administration"),
    ("users.invite", "Invite new members with a one-time link (the access role is chosen by the person inviting)", "Administration"),
    ("email.manage", "Set up email (sender name, reply-to, signature, test email); see every sent email and send failed ones again", "Administration"),
]
CODES = [p[0] for p in PERMISSIONS]

_ALL = frozenset(CODES)

# Roles that hold every permission, today and for every code added later. Saved matrix decisions cannot reduce them.
ALL_ACCESS_ROLES = frozenset({ADMIN, MANAGING_PARTNER, PARTNER})

DEFAULT_MATRIX = {
    ADMIN: _ALL,
    MANAGING_PARTNER: _ALL,
    PARTNER: _ALL,
    FINANCE_MANAGER: frozenset({
        "company.view", "finance.manage", "documents.issue", "documents.view_confidential", "sales.view", "sales.manage",
        "sales.issue", "accounting.view", "accounting.manage", "purchases.view", "purchases.manage", "tax.view", "tax.manage",
        "hr.view", "hr.payroll", "projects.view", "approvals.view", "compliance.view", "compliance.manage", "partners.view",
        "workplace.view", "reports.view",
    }),
    HR_MANAGER: frozenset({
        "company.view", "documents.issue", "hr.view", "hr.manage", "hr.payroll", "projects.view", "approvals.view",
        "workplace.view", "workplace.manage", "reports.view", "users.invite",
    }),
    PROJECT_MANAGER: frozenset({
        "company.view", "documents.issue", "sales.view", "purchases.view", "hr.view", "projects.view", "projects.manage",
        "approvals.view", "workplace.view", "workplace.manage", "crm.view", "reports.view",
    }),
    EMPLOYEE: frozenset({"company.view", "documents.issue", "workplace.view"}),
}

# Bumped whenever the matrix is saved, so cached permission sets are recomputed.
_version = 0


def matrix_changed():
    global _version
    _version += 1


def current_version():
    return _version


def effective_matrix():
    """{role: set of codes} with saved decisions applied over the defaults."""
    from .models import RolePermission

    matrix = {role: set(DEFAULT_MATRIX.get(role, ())) for role in ROLE_VALUES}
    for row in RolePermission.objects.all():
        if row.role in matrix and row.code in _ALL:
            (matrix[row.role].add if row.granted else matrix[row.role].discard)(row.code)
    for role in ALL_ACCESS_ROLES:
        matrix[role] = set(_ALL)  # never reduced
    return matrix


def codes_for_role(role):
    if role in ALL_ACCESS_ROLES:
        return set(_ALL)
    return effective_matrix().get(role, set())


def roles_with(code):
    """Roles that hold `code`: who could, for example, be a second approver."""
    return [role for role, codes in effective_matrix().items() if code in codes]


class HasCode(BasePermission):
    """DRF permission: the signed-in user holds `code`. Use `HasCode.for_code("users.manage")` for a class."""

    code = ""

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.has_code(self.code))

    @classmethod
    def for_code(cls, code):
        return type(f"Has_{code.replace('.', '_')}", (cls,), {"code": code})


# Who may be given which access role by an invitation (`users.invite`). The inviter chooses the role; the invitee never does,
# because letting an invitee pick their own access role would be a privilege-escalation hole.
#   admin: any role.   managing_partner / partner: any role except admin.   hr_manager: only employee and project_manager.
# A role that holds `users.invite` through the matrix but is not listed here (a custom grant) may invite only employees.
INVITE_GRANTS = {
    ADMIN: tuple(ROLE_VALUES),
    MANAGING_PARTNER: tuple(r for r in ROLE_VALUES if r != ADMIN),
    PARTNER: tuple(r for r in ROLE_VALUES if r != ADMIN),
    HR_MANAGER: (PROJECT_MANAGER, EMPLOYEE),
}


def invite_roles_for(user):
    """Access roles `user` may give through an invitation (values, in the order of `ROLES`)."""
    if not (user and user.is_authenticated and user.has_code("users.invite")):
        return []
    if user.is_superuser:
        return list(INVITE_GRANTS[ADMIN])
    allowed = INVITE_GRANTS.get(user.role, (EMPLOYEE,))
    return [r for r in ROLE_VALUES if r in allowed]
