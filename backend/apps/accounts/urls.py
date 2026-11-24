from django.urls import path

from . import invite_views, views

urlpatterns = [
    path("login/", views.LoginView.as_view(), name="auth-login"),
    path("logout/", views.LogoutView.as_view(), name="auth-logout"),
    path("me/", views.MeView.as_view(), name="auth-me"),
    path("users/", views.UserCollectionView.as_view(), name="auth-users"),
    path("users/<uuid:pk>/", views.UserDetailView.as_view(), name="auth-user"),
    path("users/<uuid:pk>/set-password/", views.UserPasswordView.as_view(), name="auth-user-password"),
    path("access/", views.AccessMatrixView.as_view(), name="auth-access"),
    path("access/reset/", views.AccessResetView.as_view(), name="auth-access-reset"),
    path("login-history/", views.LoginHistoryView.as_view(), name="auth-login-history"),
    path("activity/", views.ActivityView.as_view(), name="auth-activity"),
    path("invitations/", invite_views.InvitationCollectionView.as_view(), name="auth-invitations"),
    path("invitations/lookup/", invite_views.InvitationLookupView.as_view(), name="auth-invitation-lookup"),
    path("invitations/accept/", invite_views.InvitationAcceptView.as_view(), name="auth-invitation-accept"),
    path("invitations/<uuid:pk>/revoke/", invite_views.InvitationRevokeView.as_view(), name="auth-invitation-revoke"),
    path("invitations/<uuid:pk>/regenerate/", invite_views.InvitationRegenerateView.as_view(), name="auth-invitation-regenerate"),
    path("invitations/<uuid:pk>/email-draft/", invite_views.InvitationEmailDraftView.as_view(), name="auth-invitation-email-draft"),
    path("invitations/<uuid:pk>/email/", invite_views.InvitationEmailView.as_view(), name="auth-invitation-email"),
]
