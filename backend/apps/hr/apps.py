from django.apps import AppConfig


class HrConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.hr"

    def ready(self):
        from apps.attachments import registry
        from apps.company.services import audit

        E = audit.EntityAudit
        personal = frozenset({"date_of_birth", "personal_email", "work_email", "phone", "address", "emergency_name", "emergency_relation",
                              "emergency_phone", "pan", "aadhaar", "bank_account", "bank_name", "bank_ifsc", "bank_account_holder",
                              "uan", "esi_number"})
        pay = frozenset({"basic", "hra", "special_allowance", "other_allowance", "fixed_deductions", "tds_monthly", "bonus", "tds",
                         "loan_recovery", "other_deductions", "net", "total", "amount"})
        for name, spec in {
            "employee": E("Employee", "hr", redact=personal, bump_version=False),
            "department": E("Department", "hr", bump_version=False),
            "designation": E("Designation", "hr", bump_version=False),
            "salary_structure": E("Salary structure", "hr", redact=pay, bump_version=False),
            "salary_revision": E("Salary revision", "hr", redact=pay | {"monthly_gross", "annual_increase"}, bump_version=False),
            "advance": E("Employee advance", "hr", redact=pay, bump_version=False),
            "holiday": E("Holiday", "hr", bump_version=False),
            "attendance": E("Attendance", "hr", bump_version=False),
            "leave_type": E("Leave type", "hr", bump_version=False),
            "leave_request": E("Leave request", "hr", bump_version=False),
            "leave_adjustment": E("Leave adjustment", "hr", bump_version=False),
            "payroll_settings": E("Payroll settings", "hr", bump_version=False),
            "payroll_run": E("Payroll run", "hr", redact=frozenset({"net"}), bump_version=False),
            "payslip": E("Payslip", "hr", redact=pay, bump_version=False),
            "review_cycle": E("Review cycle", "hr", bump_version=False),
            "review": E("Performance review", "hr", bump_version=False),
            "goal": E("Goal", "hr", bump_version=False),
            "assignment": E("Project assignment", "hr", bump_version=False),
        }.items():
            audit.register_entity(name, spec)
        registry.register("employee", view="hr.view", manage="hr.manage", label="Employee")
        from . import approval_hooks

        approval_hooks.register()
