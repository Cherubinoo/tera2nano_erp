from django.urls import path

from . import views

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
]
