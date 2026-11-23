from django.urls import path

from . import views as v

urlpatterns = [
    path("dashboard/", v.DashboardView.as_view(), name="workplace-dashboard"),
    path("people/", v.PeopleView.as_view(), name="workplace-people"),
    # meetings
    path("meetings/", v.MeetingCollectionView.as_view(), name="workplace-meetings"),
    path("meetings/<uuid:pk>/", v.MeetingDetailView.as_view(), name="workplace-meeting"),
    path("meetings/<uuid:pk>/status/", v.MeetingStatusView.as_view(), name="workplace-meeting-status"),
    path("meetings/<uuid:pk>/reschedule/", v.MeetingRescheduleView.as_view(), name="workplace-meeting-reschedule"),
    path("meetings/<uuid:pk>/generate/", v.MeetingGenerateView.as_view(), name="workplace-meeting-generate"),
    path("meetings/<uuid:pk>/decisions/", v.MeetingDecisionView.as_view(), name="workplace-meeting-decisions"),
    path("meetings/<uuid:pk>/decisions/<uuid:decision_id>/", v.MeetingDecisionDetailView.as_view(), name="workplace-meeting-decision"),
    path("meetings/<uuid:pk>/actions/", v.MeetingActionView.as_view(), name="workplace-meeting-actions"),
    path("meetings/<uuid:pk>/minutes/", v.MeetingMinutesView.as_view(), name="workplace-meeting-minutes"),
    path("meetings/<uuid:pk>/minutes/<uuid:document_id>/download/", v.MeetingMinutesFileView.as_view(), name="workplace-meeting-minutes-file"),
    # tasks
    path("tasks/", v.TaskCollectionView.as_view(), name="workplace-tasks"),
    path("tasks/analytics/", v.TaskAnalyticsView.as_view(), name="workplace-task-analytics"),
    path("tasks/<uuid:pk>/", v.TaskDetailView.as_view(), name="workplace-task"),
    path("tasks/<uuid:pk>/status/", v.TaskStatusView.as_view(), name="workplace-task-status"),
    path("tasks/<uuid:pk>/comments/", v.TaskCommentView.as_view(), name="workplace-task-comments"),
    path("tasks/<uuid:pk>/comments/<uuid:comment_id>/", v.TaskCommentDetailView.as_view(), name="workplace-task-comment"),
    path("tasks/<uuid:pk>/items/", v.TaskItemView.as_view(), name="workplace-task-items"),
    path("tasks/<uuid:pk>/items/<uuid:item_id>/", v.TaskItemDetailView.as_view(), name="workplace-task-item"),
    # announcements and circulars
    path("announcements/", v.AnnouncementCollectionView.as_view(), name="workplace-announcements"),
    path("announcements/<uuid:pk>/", v.AnnouncementDetailView.as_view(), name="workplace-announcement"),
    path("circulars/", v.CircularCollectionView.as_view(), name="workplace-circulars"),
    path("circulars/<uuid:pk>/", v.CircularDetailView.as_view(), name="workplace-circular"),
    path("circulars/<uuid:pk>/issue/", v.CircularIssueView.as_view(), name="workplace-circular-issue"),
    path("circulars/<uuid:pk>/acknowledge/", v.CircularAcknowledgeView.as_view(), name="workplace-circular-ack"),
    path("circulars/<uuid:pk>/acknowledgements/", v.CircularAcknowledgementsView.as_view(), name="workplace-circular-acks"),
    path("circulars/<uuid:pk>/pdf/", v.CircularPdfView.as_view(), name="workplace-circular-pdf"),
    # admin registers
    path("assets/", v.AssetCollectionView.as_view(), name="workplace-assets"),
    path("assets/<uuid:pk>/", v.AssetDetailView.as_view(), name="workplace-asset"),
    path("visitors/", v.VisitorCollectionView.as_view(), name="workplace-visitors"),
    path("visitors/<uuid:pk>/", v.VisitorDetailView.as_view(), name="workplace-visitor"),
    path("visitors/<uuid:pk>/checkout/", v.VisitorCheckoutView.as_view(), name="workplace-visitor-checkout"),
]
