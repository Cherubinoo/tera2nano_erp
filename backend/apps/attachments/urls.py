from django.urls import path

from . import views

urlpatterns = [
    path("", views.AttachmentCollectionView.as_view(), name="attachments"),
    path("<uuid:pk>/", views.AttachmentDetailView.as_view(), name="attachment"),
    path("<uuid:pk>/download/", views.AttachmentDownloadView.as_view(), name="attachment-download"),
]
