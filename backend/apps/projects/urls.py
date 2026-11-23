from django.urls import path

from . import views

urlpatterns = [
    path("lookup/", views.LookupView.as_view(), name="projects-lookup"),
    path("options/", views.OptionsView.as_view(), name="projects-options"),
    path("dashboard/", views.DashboardView.as_view(), name="projects-dashboard"),
    path("portfolio/", views.PortfolioView.as_view(), name="projects-portfolio"),
    path("projects/", views.ProjectCollectionView.as_view(), name="projects"),
    path("projects/<uuid:pk>/", views.ProjectDetailView.as_view(), name="project"),
    path("projects/<uuid:pk>/status/", views.ProjectStatusView.as_view(), name="project-status"),
    path("projects/<uuid:pk>/financials/", views.FinancialsView.as_view(), name="project-financials"),
    path("projects/<uuid:pk>/budget/", views.BudgetView.as_view(), name="project-budget"),
    path("projects/<uuid:pk>/budget-changes/", views.BudgetRevisionCollectionView.as_view(), name="project-budget-changes"),
    path("budget-changes/<uuid:pk>/approve/", views.BudgetRevisionActionView.as_view(action="approve")),
    path("budget-changes/<uuid:pk>/reject/", views.BudgetRevisionActionView.as_view(action="reject")),
    path("budget-changes/<uuid:pk>/withdraw/", views.BudgetRevisionActionView.as_view(action="withdraw")),
    path("projects/<uuid:pk>/invoices/", views.InvoicesView.as_view(), name="project-invoices"),
    path("projects/<uuid:pk>/expenses/", views.ExpensesView.as_view(), name="project-expenses"),
    path("projects/<uuid:pk>/activity/", views.ActivityView.as_view(), name="project-activity"),
    path("milestones/", views.MilestoneCollectionView.as_view(), name="project-milestones"),
    path("milestones/<uuid:pk>/", views.MilestoneDetailView.as_view(), name="project-milestone"),
    path("milestones/<uuid:pk>/create-invoice/", views.MilestoneInvoiceView.as_view(), name="project-milestone-invoice"),
    path("tasks/", views.TaskCollectionView.as_view(), name="project-tasks"),
    path("tasks/<uuid:pk>/", views.TaskDetailView.as_view(), name="project-task"),
    path("team/", views.TeamCollectionView.as_view(), name="project-team"),
    path("team/<uuid:pk>/", views.TeamDetailView.as_view(), name="project-team-member"),
    path("timesheets/", views.TimesheetCollectionView.as_view(), name="project-timesheets"),
    path("timesheets/<uuid:pk>/", views.TimesheetDetailView.as_view(), name="project-timesheet"),
]
