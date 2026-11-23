from django.urls import path

from . import views as v

urlpatterns = [
    path("home/", v.HomeView.as_view()),
    path("dashboard/", v.DashboardView.as_view()),
    path("<slug:key>/", v.ReportView.as_view()),
    path("<slug:key>/export/", v.ExportView.as_view()),
]
