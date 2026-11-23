import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../api';
import { HBars } from '../../components/charts';
import { asList } from '../../hooks';
import { Badge, ErrorBox, Loading, PageHeader, Tabs, useConfirm, useToast } from '../../ui';
import { FormDialog, Gate, H, options, useCan, useData } from './common';

function NamedTable({ kind, label, rows, canManage, reload, people }) {
  const notify = useToast();
  const { confirm, dialog } = useConfirm();
  const [editing, setEditing] = useState(null);
  const isDept = kind === 'departments';
  const fields = [
    { name: 'name', label: `${label} name`, required: true, maxLength: 120 },
    ...(isDept ? [{ name: 'description', label: 'Description', wide: true }, { name: 'head', label: 'Head of department', type: 'select', options: options(people, (p) => p.name), blankLabel: '— none —' }] : []),
    ...(editing?.id ? [{ name: 'is_active', label: 'Active', type: 'checkbox' }] : []),
  ];
  const remove = async (r) => {
    if (!(await confirm({ title: `Delete ${r.name}?`, body: <p>This cannot be undone. A {label.toLowerCase()} with people in it cannot be deleted; switch it off instead.</p>, confirmLabel: 'Delete', danger: true }))) return;
    try { await api.del(`${H}/${kind}/${r.id}/`); notify(`${label} deleted.`); reload(); } catch (err) { notify(err.message, 'error'); }
  };
  return (
    <>
      <div className="card-head"><h2>{label}s</h2>{canManage && <button type="button" className="btn btn-primary" onClick={() => setEditing({})}>New {label.toLowerCase()}</button>}</div>
      {rows.length === 0 ? <p className="empty">None yet.</p> : (
        <div className="table-wrap"><table>
          <thead><tr><th>{label}</th>{isDept && <th>Head</th>}<th className="num">People</th><th>Status</th>{canManage && <th />}</tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.id} className={r.is_active ? '' : 'row-inactive'}>
              <td data-label={label}><strong>{r.name}</strong>{r.description && <div className="hint">{r.description}</div>}</td>
              {isDept && <td data-label="Head">{r.head ? <Link to={`/hr/employees/${r.head.id}`}>{r.head.name}</Link> : '—'}</td>}
              <td className="num" data-label="People"><Link to={`/hr/employees?${isDept ? 'department' : 'designation'}=${r.id}`}>{r.headcount}</Link></td>
              <td data-label="Status">{r.is_active ? <Badge tone="good">Active</Badge> : <Badge>Switched off</Badge>}</td>
              {canManage && <td className="hr-row-actions"><button type="button" className="btn btn-small" onClick={() => setEditing(r)}>Edit</button><button type="button" className="btn btn-small btn-danger-outline" onClick={() => remove(r)}>Delete</button></td>}
            </tr>
          ))}</tbody>
        </table></div>
      )}
      {editing && (
        <FormDialog title={editing.id ? `Edit ${editing.name}` : `New ${label.toLowerCase()}`} wide={false} fields={fields} record={editing.id ? { ...editing, head: editing.head?.id || '' } : { is_active: true }}
          onSubmit={async (p) => {
            const body = { ...p, ...(p.head === '' || p.head === undefined ? (isDept ? { head: null } : {}) : {}) };
            if (editing.id) await api.patch(`${H}/${kind}/${editing.id}/`, body); else await api.post(`${H}/${kind}/`, body);
            notify(`${label} saved.`); setEditing(null); reload();
          }} onClose={() => setEditing(null)} />
      )}
      {dialog}
    </>
  );
}

function Tree({ nodes }) {
  return (
    <ul className="hr-tree">
      {nodes.map((n) => (
        <li key={n.id}>
          <Link className="hr-node" to={`/hr/employees/${n.id}`}><strong>{n.name}</strong><small>{[n.designation, n.department].filter(Boolean).join(', ') || n.code}</small></Link>
          {n.reports.length > 0 && <Tree nodes={n.reports} />}
        </li>
      ))}
    </ul>
  );
}

export default function Departments() {
  const can = useCan();
  const [tab, setTab] = useState('departments');
  const deps = useData(`${H}/departments/`);
  const desigs = useData(`${H}/designations/`);
  const chart = useData(`${H}/employees/org-chart/`, undefined, tab === 'org');
  const people = useData(`${H}/lookup/`);
  return (
    <>
      <PageHeader title="Departments and designations" description="How the company is organised, and who reports to whom." />
      <Tabs tabs={[{ key: 'departments', label: 'Departments' }, { key: 'designations', label: 'Designations' }, { key: 'org', label: 'Organisation chart' }]} value={tab} onChange={setTab} />
      {tab === 'departments' && (
        <>
          <section className="card" aria-label="Headcount by department">
            <h2>Headcount</h2>
            <HBars rows={asList(deps.data).filter((d) => d.headcount > 0).map((d) => ({ key: d.id, label: d.name, value: d.headcount }))} format={(v) => String(v)} empty="No one is assigned to a department yet." />
          </section>
          <section className="card">
            <Gate state={deps}>{(rows) => <NamedTable kind="departments" label="Department" rows={rows} canManage={can('hr.manage')} reload={deps.reload} people={asList(people.data)} />}</Gate>
          </section>
        </>
      )}
      {tab === 'designations' && (
        <section className="card">
          <Gate state={desigs}>{(rows) => <NamedTable kind="designations" label="Designation" rows={rows} canManage={can('hr.manage')} reload={desigs.reload} people={[]} />}</Gate>
        </section>
      )}
      {tab === 'org' && (
        <section className="card" aria-label="Organisation chart">
          <h2>Organisation chart</h2>
          <p className="muted">Everyone who has not left, under their reporting manager.</p>
          {chart.loading ? <Loading /> : chart.error ? <ErrorBox error={chart.error} onRetry={chart.reload} /> : asList(chart.data).length === 0 ? <p className="empty">No employees yet.</p> : <Tree nodes={chart.data} />}
        </section>
      )}
    </>
  );
}
