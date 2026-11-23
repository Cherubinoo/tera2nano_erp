import './hr.css';
import Assignments from './Assignments';
import Attendance, { MyAttendance } from './Attendance';
import Dashboard from './Dashboard';
import Departments from './Departments';
import EmployeeDetail from './EmployeeDetail';
import Employees from './Employees';
import Holidays from './Holidays';
import Leave, { MyLeave } from './Leave';
import MyProfile from './MyProfile';
import Payroll, { MyPayslips } from './Payroll';
import PayrollRun from './PayrollRun';
import Performance from './Performance';

// "People": employees, attendance, leave, payroll, performance and project assignments. The module is open to every
// signed-in user (perm: null) because the "My ..." pages are self-service; each API call enforces its own permission and each
// navigation item below is shown only to the roles that can use it.
export default {
  key: 'hr',
  label: 'People',
  basePath: '/hr',
  perm: null,
  order: 60,
  nav: [
    { to: '/hr', label: 'HR Dashboard', end: true, perms: ['hr.view'] },
    { to: '/hr/employees', label: 'Employees', perms: ['hr.view'] },
    { to: '/hr/departments', label: 'Departments', perms: ['hr.view'] },
    { to: '/hr/attendance', label: 'Attendance', perms: ['hr.view'] },
    { to: '/hr/leave', label: 'Leave', perms: ['hr.view'] },
    { to: '/hr/payroll', label: 'Payroll', perms: ['hr.payroll'] },
    { to: '/hr/performance', label: 'Performance' },
    { to: '/hr/assignments', label: 'Project Assignments', perms: ['hr.view', 'projects.view'] },
    { to: '/hr/holidays', label: 'Holidays' },
    { to: '/hr/my-profile', label: 'My Profile' },
    { to: '/hr/my-attendance', label: 'My Attendance' },
    { to: '/hr/my-leave', label: 'My Leave' },
    { to: '/hr/my-payslips', label: 'My Payslips' },
  ],
  routes: [
    { index: true, element: <Dashboard /> },
    { path: 'employees', element: <Employees /> },
    { path: 'employees/:id', element: <EmployeeDetail /> },
    { path: 'departments', element: <Departments /> },
    { path: 'attendance', element: <Attendance /> },
    { path: 'leave', element: <Leave /> },
    { path: 'payroll', element: <Payroll /> },
    { path: 'payroll/runs/:id', element: <PayrollRun /> },
    { path: 'performance', element: <Performance /> },
    { path: 'assignments', element: <Assignments /> },
    { path: 'holidays', element: <Holidays /> },
    { path: 'my-profile', element: <MyProfile /> },
    { path: 'my-attendance', element: <MyAttendance /> },
    { path: 'my-leave', element: <MyLeave /> },
    { path: 'my-payslips', element: <MyPayslips /> },
  ],
  quickCreate: [
    { to: '/hr/employees?new=1', label: 'Employee', perms: ['hr.manage'] },
    { to: '/hr/my-leave', label: 'Leave request' },
    { to: '/hr/payroll', label: 'Payroll run', perms: ['hr.payroll'] },
  ],
};
