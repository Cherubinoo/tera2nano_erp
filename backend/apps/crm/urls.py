from django.urls import path

from . import views_deals as d, views_leads as lv, views_misc as m, views_people as p

urlpatterns = [
    path("lookups/", m.LookupsView.as_view(), name="crm-lookups"),
    path("dashboard/", m.DashboardView.as_view(), name="crm-dashboard"),
    path("reports/summary/", m.ReportSummaryView.as_view(), name="crm-report-summary"),
    path("reports/<str:kind>.csv", m.ExportView.as_view(), name="crm-export"),
    # leads
    path("leads/", lv.LeadCollectionView.as_view(), name="crm-leads"),
    path("leads/bulk/", lv.LeadBulkView.as_view(), name="crm-leads-bulk"),
    path("leads/import/", lv.LeadImportView.as_view(), name="crm-leads-import"),
    path("leads/<uuid:pk>/", lv.LeadDetailView.as_view(), name="crm-lead"),
    path("leads/<uuid:pk>/timeline/", lv.LeadTimelineView.as_view(), name="crm-lead-timeline"),
    path("leads/<uuid:pk>/convert/", lv.LeadConvertView.as_view(), name="crm-lead-convert"),
    path("duplicates/", lv.DuplicateCheckView.as_view(), name="crm-duplicates"),
    # accounts and contacts
    path("accounts/", p.AccountCollectionView.as_view(), name="crm-accounts"),
    path("accounts/<uuid:pk>/", p.AccountDetailView.as_view(), name="crm-account"),
    path("accounts/<uuid:pk>/timeline/", p.AccountTimelineView.as_view(), name="crm-account-timeline"),
    path("contacts/", p.ContactCollectionView.as_view(), name="crm-contacts"),
    path("contacts/<uuid:pk>/", p.ContactDetailView.as_view(), name="crm-contact"),
    path("contacts/<uuid:pk>/timeline/", p.ContactTimelineView.as_view(), name="crm-contact-timeline"),
    # deals
    path("deals/", d.DealCollectionView.as_view(), name="crm-deals"),
    path("deals/board/", d.DealBoardView.as_view(), name="crm-deals-board"),
    path("deals/<uuid:pk>/", d.DealDetailView.as_view(), name="crm-deal"),
    path("deals/<uuid:pk>/move/", d.DealMoveView.as_view(), name="crm-deal-move"),
    path("deals/<uuid:pk>/items/", d.DealItemsView.as_view(), name="crm-deal-items"),
    path("deals/<uuid:pk>/customer/", d.DealCustomerView.as_view(), name="crm-deal-customer"),
    path("deals/<uuid:pk>/timeline/", d.DealTimelineView.as_view(), name="crm-deal-timeline"),
    # activities
    path("activities/", m.ActivityCollectionView.as_view(), name="crm-activities"),
    path("activities/<uuid:pk>/", m.ActivityDetailView.as_view(), name="crm-activity"),
    path("activities/<uuid:pk>/complete/", m.ActivityCompleteView.as_view(), name="crm-activity-complete"),
    path("activities/<uuid:pk>/reopen/", m.ActivityReopenView.as_view(), name="crm-activity-reopen"),
    # products and settings
    path("products/", m.ProductListView.as_view(), name="crm-products"),
    path("products/<uuid:pk>/", m.ProductDetailView.as_view(), name="crm-product"),
    path("settings/stages/", m.StageListView.as_view(), name="crm-stages"),
    path("settings/stages/reorder/", m.StageReorderView.as_view(), name="crm-stages-reorder"),
    path("settings/stages/<uuid:pk>/", m.StageDetailView.as_view(), name="crm-stage"),
    path("settings/sources/", m.SourceListView.as_view(), name="crm-sources"),
    path("settings/sources/<uuid:pk>/", m.SourceDetailView.as_view(), name="crm-source"),
    path("settings/lost-reasons/", m.LostReasonListView.as_view(), name="crm-lost-reasons"),
    path("settings/lost-reasons/<uuid:pk>/", m.LostReasonDetailView.as_view(), name="crm-lost-reason"),
]
