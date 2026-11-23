from django.urls import path

from . import views_misc as misc
from . import views_payroll as pay
from . import views_people as ppl
from . import views_time as tm

urlpatterns = [
    path("lookup/", ppl.LookupView.as_view()),
    path("dashboard/", misc.DashboardView.as_view()),
    path("me/", ppl.MeView.as_view()),
    path("me/attendance/", tm.MyAttendance.as_view()),
    path("me/check-in/", tm.MyCheckIn.as_view()),
    path("me/check-out/", tm.MyCheckOut.as_view()),
    path("me/payslips/", pay.MyPayslips.as_view()),

    path("departments/", ppl.DepartmentCollection.as_view()),
    path("departments/<uuid:pk>/", ppl.DepartmentDetail.as_view()),
    path("designations/", ppl.DesignationCollection.as_view()),
    path("designations/<uuid:pk>/", ppl.DesignationDetail.as_view()),
    path("employees/", ppl.EmployeeCollection.as_view()),
    path("employees/org-chart/", ppl.OrgChartView.as_view()),
    path("employees/available-users/", ppl.AvailableUsersView.as_view()),
    path("employees/<uuid:pk>/", ppl.EmployeeDetail.as_view()),
    path("employees/<uuid:pk>/salary/", ppl.SalaryCollection.as_view()),
    path("employees/<uuid:pk>/salary/<uuid:sid>/", ppl.SalaryDetail.as_view()),
    path("employees/<uuid:pk>/salary-revisions/<uuid:rid>/approve/", ppl.SalaryRevisionAction.as_view(action="approve")),
    path("employees/<uuid:pk>/salary-revisions/<uuid:rid>/reject/", ppl.SalaryRevisionAction.as_view(action="reject")),
    path("employees/<uuid:pk>/salary-revisions/<uuid:rid>/withdraw/", ppl.SalaryRevisionAction.as_view(action="withdraw")),
    path("employees/<uuid:pk>/advances/", ppl.AdvanceCollection.as_view()),
    path("employees/<uuid:pk>/advances/<uuid:aid>/", ppl.AdvanceDetail.as_view()),
    path("employees/<uuid:pk>/attendance/", tm.EmployeeAttendance.as_view()),
    path("employees/<uuid:pk>/payslips/", pay.EmployeePayslips.as_view()),

    path("holidays/", tm.HolidayCollection.as_view()),
    path("holidays/<uuid:pk>/", tm.HolidayDetail.as_view()),
    path("attendance/", tm.AttendanceCollection.as_view()),
    path("attendance/grid/", tm.AttendanceGrid.as_view()),
    path("attendance/<uuid:pk>/", tm.AttendanceDetail.as_view()),

    path("leave/types/", tm.LeaveTypeCollection.as_view()),
    path("leave/types/<uuid:pk>/", tm.LeaveTypeDetail.as_view()),
    path("leave/balances/", tm.LeaveBalances.as_view()),
    path("leave/calendar/", tm.LeaveCalendar.as_view()),
    path("leave/adjustments/", tm.LeaveAdjustmentCollection.as_view()),
    path("leave/requests/", tm.LeaveRequestCollection.as_view()),
    path("leave/requests/<uuid:pk>/", tm.LeaveRequestDetail.as_view()),
    *[path(f"leave/requests/<uuid:pk>/{a}/", tm.LeaveRequestAction.as_view(action=a)) for a in ("submit", "approve", "reject", "cancel")],

    path("payroll/settings/", pay.SettingsView.as_view()),
    path("payroll/runs/", pay.RunCollection.as_view()),
    path("payroll/runs/<uuid:pk>/", pay.RunDetail.as_view()),
    path("payroll/runs/<uuid:pk>/bank-file/", pay.BankFile.as_view()),
    *[path(f"payroll/runs/<uuid:pk>/{a}/", pay.RunAction.as_view(action=a))
      for a in ("regenerate", "lock", "unlock", "submit", "approve", "reject", "post", "pay", "void")],
    path("payroll/payslips/<uuid:pk>/", pay.PayslipDetail.as_view()),
    path("payroll/payslips/<uuid:pk>/pdf/", pay.PayslipPdf.as_view()),
    path("payroll/reports/monthly-cost/", pay.MonthlyCostReport.as_view()),
    path("payroll/reports/department-cost/", pay.DepartmentCostReport.as_view()),
    path("payroll/reports/statutory/", pay.StatutoryReport.as_view()),
    path("payroll/reports/register/", pay.RegisterReport.as_view()),
    path("payroll/reports/ytd/", pay.YtdReport.as_view()),

    path("performance/cycles/", misc.CycleCollection.as_view()),
    path("performance/cycles/<uuid:pk>/", misc.CycleDetail.as_view()),
    path("performance/cycles/<uuid:pk>/summary/", misc.CycleSummary.as_view()),
    path("performance/cycles/<uuid:pk>/launch/", misc.CycleAction.as_view(action="launch")),
    path("performance/cycles/<uuid:pk>/close/", misc.CycleAction.as_view(action="close")),
    path("performance/reviews/", misc.ReviewCollection.as_view()),
    path("performance/reviews/<uuid:pk>/self/", misc.ReviewAction.as_view(kind="self")),
    path("performance/reviews/<uuid:pk>/manager/", misc.ReviewAction.as_view(kind="manager")),
    path("performance/goals/", misc.GoalCollection.as_view()),
    path("performance/goals/<uuid:pk>/", misc.GoalDetail.as_view()),

    path("assignments/", misc.AssignmentCollection.as_view()),
    path("assignments/utilisation/", misc.UtilisationView.as_view()),
    path("assignments/<uuid:pk>/", misc.AssignmentDetail.as_view()),
]
