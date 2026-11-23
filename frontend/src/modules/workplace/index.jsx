import './workplace.css';
import AnnouncementsPage from './Announcements';
import AssetsPage from './Assets';
import { CircularDetail, CircularsPage } from './Circulars';
import WorkplaceHome from './Home';
import MeetingDetail from './MeetingDetail';
import MeetingsPage from './Meetings';
import TasksPage, { MyTasksPage } from './Tasks';
import VisitorsPage from './Visitors';

// Meetings, tasks, announcements, circulars and office admin. Open to every signed-in user (perm: null): each API
// enforces its own permission, and the sidebar only lists what the user may use.
export default {
  key: 'workplace',
  label: 'Workplace',
  basePath: '/workplace',
  perm: null,
  order: 90,
  nav: [
    { to: '/workplace', label: 'Workplace Home', end: true },
    { to: '/workplace/meetings', label: 'Meetings', perms: ['workplace.view'] },
    { to: '/workplace/tasks', label: 'Tasks', perms: ['workplace.view'] },
    { to: '/workplace/my-tasks', label: 'My Tasks' },
    { to: '/workplace/announcements', label: 'Announcements' },
    { to: '/workplace/circulars', label: 'Circulars', perms: ['workplace.view'] },
    { to: '/workplace/assets', label: 'Assets', perms: ['workplace.manage'] },
    { to: '/workplace/visitors', label: 'Visitors', perms: ['workplace.manage'] },
  ],
  routes: [
    { index: true, element: <WorkplaceHome /> },
    { path: 'meetings', element: <MeetingsPage /> },
    { path: 'meetings/:id', element: <MeetingDetail /> },
    { path: 'tasks', element: <TasksPage /> },
    { path: 'my-tasks', element: <MyTasksPage /> },
    { path: 'announcements', element: <AnnouncementsPage /> },
    { path: 'circulars', element: <CircularsPage /> },
    { path: 'circulars/:id', element: <CircularDetail /> },
    { path: 'assets', element: <AssetsPage /> },
    { path: 'visitors', element: <VisitorsPage /> },
  ],
  quickCreate: [
    { to: '/workplace/my-tasks?new=1', label: 'Task' },
    { to: '/workplace/meetings?new=1', label: 'Meeting', perms: ['workplace.manage'] },
    { to: '/workplace/announcements?new=1', label: 'Announcement', perms: ['workplace.manage'] },
    { to: '/workplace/circulars?new=1', label: 'Circular', perms: ['workplace.manage'] },
  ],
};
