from django.urls import path

from . import views

urlpatterns = [
    path("summary/", views.SummaryView.as_view(), name="approvals-summary"),
    path("kinds/", views.KindsView.as_view(), name="approvals-kinds"),
    path("users/", views.UsersView.as_view(), name="approvals-users"),
    path("dashboard/", views.DashboardView.as_view(), name="approvals-dashboard"),
    path("status/", views.StatusView.as_view(), name="approvals-status"),
    path("submit/", views.SubmitView.as_view(), name="approvals-submit"),
    path("requests/", views.RequestCollectionView.as_view(), name="approvals-requests"),
    path("requests/bulk-approve/", views.BulkApproveView.as_view(), name="approvals-bulk-approve"),
    path("requests/<uuid:pk>/", views.RequestDetailView.as_view(), name="approvals-request"),
    path("requests/<uuid:pk>/approve/", views.ApproveView.as_view(), name="approvals-approve"),
    path("requests/<uuid:pk>/reject/", views.RejectView.as_view(), name="approvals-reject"),
    path("requests/<uuid:pk>/withdraw/", views.WithdrawView.as_view(), name="approvals-withdraw"),
    path("requests/<uuid:pk>/resubmit/", views.ResubmitView.as_view(), name="approvals-resubmit"),
    path("requests/<uuid:pk>/reassign/", views.ReassignView.as_view(), name="approvals-reassign"),
    path("rules/", views.RuleCollectionView.as_view(), name="approvals-rules"),
    path("rules/reorder/", views.RuleReorderView.as_view(), name="approvals-rules-reorder"),
    path("rules/seed/", views.SeedRulesView.as_view(), name="approvals-rules-seed"),
    path("rules/<uuid:pk>/", views.RuleDetailView.as_view(), name="approvals-rule"),
]
