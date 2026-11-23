from django.urls import path

from . import views as v

urlpatterns = [
    path("lookups/", v.LookupsView.as_view()),
    path("settings/", v.SettingsView.as_view()),
    path("dashboard/", v.DashboardView.as_view()),
    path("me/", v.MeView.as_view()),
    path("partners/", v.PartnerCollectionView.as_view()),
    path("partners/<str:pk>/", v.PartnerDetailView.as_view()),
    path("partners/<str:pk>/statement/", v.StatementView.as_view()),
    path("partners/<str:pk>/statement/pdf/", v.StatementPdfView.as_view()),
    path("transactions/", v.TransactionCollectionView.as_view()),
    path("transactions/<str:pk>/", v.TransactionDetailView.as_view()),
    path("transactions/<str:pk>/approve/", v.TransactionActionView.as_view(action="approve")),
    path("transactions/<str:pk>/reject/", v.TransactionActionView.as_view(action="reject")),
    path("transactions/<str:pk>/void/", v.TransactionActionView.as_view(action="void")),
    path("interest-preview/", v.InterestPreviewView.as_view()),
    path("profit-shares/", v.ScheduleCollectionView.as_view()),
    path("profit-shares/<str:pk>/", v.ScheduleDetailView.as_view()),
    path("allocations/", v.AllocationCollectionView.as_view()),
    path("allocations/preview/", v.AllocationPreviewView.as_view()),
    path("allocations/<str:pk>/", v.AllocationDetailView.as_view()),
    path("allocations/<str:pk>/post/", v.AllocationActionView.as_view(action="post")),
    path("allocations/<str:pk>/reverse/", v.AllocationActionView.as_view(action="reverse")),
    path("loans/", v.LoanLedgerView.as_view()),
    path("history/", v.HistoryView.as_view()),
    path("agreements/", v.AgreementsView.as_view()),
]
