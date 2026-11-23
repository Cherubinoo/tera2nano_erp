from django.urls import path

from . import views

urlpatterns = [
    path("dashboard/", views.DashboardView.as_view(), name="tax-dashboard"),
    path("options/", views.OptionsView.as_view(), name="tax-options"),
    path("calendar/", views.CalendarView.as_view(), name="tax-calendar"),
    path("gst/summary/", views.SummaryView.as_view(), name="tax-gst-summary"),
    path("gst/sales-register/", views.SalesRegisterView.as_view(), name="tax-sales-register"),
    path("gst/sales-register/export/", views.SalesRegisterExportView.as_view(), name="tax-sales-register-export"),
    path("gst/purchase-register/", views.PurchaseRegisterView.as_view(), name="tax-purchase-register"),
    path("gst/purchase-register/export/", views.PurchaseRegisterExportView.as_view(), name="tax-purchase-register-export"),
    path("gst/purchase-register/flag/", views.PurchaseFlagView.as_view(), name="tax-purchase-flag"),
    path("gst/reconciliation/", views.ReconciliationView.as_view(), name="tax-reconciliation"),
    path("gst/liability-ledger/", views.LiabilityLedgerView.as_view(), name="tax-liability-ledger"),
    path("filings/", views.FilingCollectionView.as_view(), name="tax-filings"),
    path("filings/generate/", views.FilingGenerateView.as_view(), name="tax-filings-generate"),
    path("filings/<uuid:pk>/", views.FilingDetailView.as_view(), name="tax-filing"),
    path("filings/<uuid:pk>/prepare/", views.FilingActionView.as_view(action="prepare"), name="tax-filing-prepare"),
    path("filings/<uuid:pk>/file/", views.FilingActionView.as_view(action="file"), name="tax-filing-file"),
    path("filings/<uuid:pk>/reopen/", views.FilingActionView.as_view(action="reopen"), name="tax-filing-reopen"),
    path("filings/<uuid:pk>/setoff/", views.FilingSetoffView.as_view(), name="tax-filing-setoff"),
    path("filings/<uuid:pk>/setoff/undo/", views.FilingSetoffUndoView.as_view(), name="tax-filing-setoff-undo"),
    path("payments/", views.PaymentCollectionView.as_view(), name="tax-payments"),
    path("payments/<uuid:pk>/", views.PaymentDetailView.as_view(), name="tax-payment"),
    path("payments/<uuid:pk>/void/", views.PaymentVoidView.as_view(), name="tax-payment-void"),
    path("tds/ledger/", views.TdsLedgerView.as_view(), name="tax-tds-ledger"),
    path("tds/credits/", views.TdsCreditCollectionView.as_view(), name="tax-tds-credits"),
    path("tds/credits/<uuid:pk>/", views.TdsCreditDetailView.as_view(), name="tax-tds-credit"),
    path("income-tax/", views.IncomeTaxView.as_view(), name="tax-income-tax"),
]
