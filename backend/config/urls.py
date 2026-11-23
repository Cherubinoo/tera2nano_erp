from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/auth/", include("apps.accounts.urls")),
    path("api/v1/company/", include("apps.company.urls")),
    path("api/v1/sales/", include("apps.sales.urls")),
    path("api/v1/accounting/", include("apps.accounting.urls")),
    path("api/v1/attachments/", include("apps.attachments.urls")),
    path("api/v1/purchases/", include("apps.purchases.urls")),
    path("api/v1/tax/", include("apps.tax.urls")),
    path("api/v1/hr/", include("apps.hr.urls")),
    path("api/v1/projects/", include("apps.projects.urls")),
    path("api/v1/approvals/", include("apps.approvals.urls")),
    path("api/v1/compliance/", include("apps.compliance.urls")),
    path("api/v1/partners/", include("apps.partners.urls")),
    path("api/v1/workplace/", include("apps.workplace.urls")),
    path("api/v1/crm/", include("apps.crm.urls")),
    path("api/v1/reports/", include("apps.reports.urls")),
]
