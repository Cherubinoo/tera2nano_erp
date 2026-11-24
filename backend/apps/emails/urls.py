from django.urls import path

from . import views

urlpatterns = [
    path("status/", views.StatusView.as_view(), name="email-status"),
    path("settings/", views.SettingsView.as_view(), name="email-settings"),
    path("test/", views.TestView.as_view(), name="email-test"),
    path("draft/", views.DraftView.as_view(), name="email-draft"),
    path("send/", views.SendView.as_view(), name="email-send"),
    path("messages/", views.MessageCollectionView.as_view(), name="email-messages"),
    path("messages/<uuid:pk>/", views.MessageDetailView.as_view(), name="email-message"),
    path("messages/<uuid:pk>/resend/", views.MessageResendView.as_view(), name="email-message-resend"),
]
