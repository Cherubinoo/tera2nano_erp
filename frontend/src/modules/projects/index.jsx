import './projects.css';
import Dashboard from './Dashboard';
import Milestones from './Milestones';
import ProjectDetail from './ProjectDetail';
import Projects from './Projects';
import Profitability from './Profitability';
import Tasks from './Tasks';
import Timesheets from './Timesheets';

export default {
  key: 'projects',
  label: 'Projects',
  basePath: '/projects',
  perm: 'projects.view',
  order: 30,
  nav: [
    { to: '/projects', label: 'Projects Dashboard', end: true, perms: ['projects.view'] },
    { to: '/projects/projects', label: 'All Projects', perms: ['projects.view'] },
    { to: '/projects/tasks', label: 'Tasks', perms: ['projects.view'] },
    { to: '/projects/timesheets', label: 'Timesheets', perms: ['projects.view'] },
    { to: '/projects/milestones', label: 'Milestones', perms: ['projects.view'] },
    { to: '/projects/profitability', label: 'Project Profitability', perms: ['projects.view'] },
  ],
  routes: [
    { index: true, element: <Dashboard /> },
    { path: 'projects', element: <Projects /> },
    { path: 'projects/:id', element: <ProjectDetail /> },
    { path: 'tasks', element: <Tasks /> },
    { path: 'timesheets', element: <Timesheets /> },
    { path: 'milestones', element: <Milestones /> },
    { path: 'profitability', element: <Profitability /> },
  ],
  quickCreate: [
    { to: '/projects/projects?new=1', label: 'Project', perms: ['projects.manage'] },
    { to: '/projects/tasks?new=1', label: 'Project task', perms: ['projects.manage'] },
    { to: '/projects/timesheets?new=1', label: 'Time entry', perms: ['projects.manage'] },
  ],
};
