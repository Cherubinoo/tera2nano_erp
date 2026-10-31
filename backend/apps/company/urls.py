from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views, views_misc

router = DefaultRouter()
router.register("addresses", views.CompanyAddressViewSet, basename="address")
router.register("partners", views.CompanyPartnerViewSet, basename="partner")
router.register("signatories", views.AuthorizedSignatoryViewSet, basename="signatory")
router.register("contacts", views.CompanyContactViewSet, basename="contact")
router.register("tax-registrations", views.CompanyTaxRegistrationViewSet, basename="tax-registration")
router.register("bank-accounts", views.CompanyBankAccountViewSet, basename="bank-account")
router.register("financial-years", views.FinancialYearViewSet, basename="financial-year")
router.register("policies", views.CompanyPolicyViewSet, basename="policy")
router.register("letterhead-templates", views.LetterheadTemplateViewSet, basename="letterhead-template")
router.register("documents", views.CompanyDocumentViewSet, basename="document")
router.register("issued-documents", views.IssuedDocumentViewSet, basename="issued-document")

urlpatterns = [
    path("profile/", views_misc.CompanyProfileView.as_view(), name="company-profile"),
    path("profile/logo/", views_misc.CompanyLogoView.as_view(), name="company-logo"),
    path("overview/", views_misc.OverviewView.as_view(), name="company-overview"),
    path("lookups/", views_misc.LookupsView.as_view(), name="company-lookups"),
    path("resolve/", views_misc.ResolveView.as_view(), name="company-resolve"),
    path("setup/", views_misc.SetupStateView.as_view(), name="setup-state"),
    path("setup/start/", views_misc.SetupStartView.as_view(), name="setup-start"),
    path("setup/progress/", views_misc.SetupProgressView.as_view(), name="setup-progress"),
    path("setup/complete/", views_misc.SetupCompleteView.as_view(), name="setup-complete"),
    path("tax-settings/", views_misc.TaxSettingsView.as_view(), name="tax-settings"),
    path("tax-settings/calculate/", views_misc.TaxCalculationView.as_view(), name="tax-calculate"),
    path("quotations/working/", views_misc.QuotationWorkingView.as_view(), name="quotation-working"),
    path("branding/", views_misc.BrandingView.as_view(), name="branding"),
    path("branding/preview/", views_misc.BrandingPreviewView.as_view(), name="branding-preview"),
    path("branding/assets/<str:slot>/", views_misc.BrandingAssetView.as_view(), name="branding-asset"),
    path("document-numbering/", views_misc.NumberingCollectionView.as_view(), name="numbering"),
    path("document-numbering/next/", views_misc.NextReferenceView.as_view(), name="numbering-next"),
    path("document-numbering/seed/", views_misc.NumberingSeedView.as_view(), name="numbering-seed"),
    path("document-numbering/<uuid:pk>/", views_misc.NumberingDetailView.as_view(), name="numbering-detail"),
    path("document-numbering/<uuid:pk>/issued/", views_misc.NumberingIssuedView.as_view(), name="numbering-issued"),
    path("audit-history/", views_misc.AuditHistoryView.as_view(), name="audit-history"),
    path("", include(router.urls)),
]
