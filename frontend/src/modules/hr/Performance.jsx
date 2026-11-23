import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../api';
import { HBars } from '../../components/charts';
import { dateOrder } from '../../forms';
import { asList } from '../../hooks';
import { ErrorBox, Field, Loading, PageHeader, Tabs, fmtDate, useConfirm, useToast } from '../../ui';
import { FormDialog, Gate, H, StatusBadge, Tile, choose, options, useCan, useData } from './common';

const RATINGS = [['', '— choose —'], [5, '5, Outstanding'], [4, '4, Exceeds expectations'], [3, '3, Meets expectations'], [2, '2, Needs improvement'], [1, '1, Unsatisfactory']].map(([value, label]) => ({ value: String(value), label }));

function Cycles({ cycles, reload, selected, onSelect }) {
  const notify = useToast();
  const { confirm, dialog } = useConfirm();
  const [editing, setEditing] = useState(null);
  const act = async (c, action, done) => { try { await api.post(`${H}/performance/cycles/${c.id}/${action}/`); notify(done); reload(); } catch (err) { notify(err.message, 'error'); } };
  const launch = async (c) => { if (await confirm({ title: `Launch ${c.name}?`, body: <p>A review is created for every current employee, with their manager as the reviewer.</p>, confirmLabel: 'Launch' })) act(c, 'launch', 'Cycle launched.'); };
  const close = async (c) => { if (await confirm({ title: `Close ${c.name}?`, body: <p>No more ratings or goal changes can be made.</p>, confirmLabel: 'Close cycle' })) act(c, 'close', 'Cycle closed.'); };
  const del = async (c) => { try { await api.del(`${H}/performance/cycles/${c.id}/`); notify('Cycle deleted.'); reload(); } catch (err) { notify(err.message, 'error'); } };
  return (
    <section className="card" aria-label="Review cycles">
      <div className="card-head"><h2>Review cycles</h2><button type="button" className="btn btn-primary" onClick={() => setEditing({})}>New cycle</button></div>
      {cycles.length === 0 ? <p className="empty">No review cycle yet. Create one for the period you want to review.</p> : (
        <div className="table-wrap"><table>
          <thead><tr><th>Cycle</th><th>Period</th><th className="num">Reviews done</th><th>Status</th><th /></tr></thead>
          <tbody>{cycles.map((c) => (
            <tr key={c.id} className={selected === c.id ? 'row-edit' : ''}>
              <td data-label="Cycle"><button type="button" className="btn btn-small" onClick={() => onSelect(c.id)} aria-pressed={selected === c.id}><strong>{c.name}</strong></button></td>
              <td data-label="Period">{fmtDate(c.start_date)} to {fmtDate(c.end_date)}</td>
              <td className="num" data-label="Reviews done">{c.completed} of {c.reviews}</td><td data-label="Status"><StatusBadge status={c.status} label={c.status_label} /></td>
              <td className="hr-row-actions">
                {c.status === 'draft' && <button type="button" className="btn btn-small btn-primary" onClick={() => launch(c)}>Launch</button>}
                {c.status === 'open' && <button type="button" className="btn btn-small" onClick={() => close(c)}>Close</button>}
                {c.status !== 'closed' && <button type="button" className="btn btn-small" onClick={() => setEditing(c)}>Edit</button>}
                {c.status === 'draft' && <button type="button" className="btn btn-small btn-danger-outline" onClick={() => del(c)}>Delete</button>}
              </td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
      {editing && (
        <FormDialog title={editing.id ? `Edit ${editing.name}` : 'New review cycle'} wide={false} record={editing.id ? editing : undefined}
          fields={[{ name: 'name', label: 'Name', required: true, hint: 'For example "FY 2026-27 first half".', maxLength: 120, wide: true }, { name: 'start_date', label: 'Period starts', type: 'date', required: true },
            { name: 'end_date', label: 'Period ends', type: 'date', required: true, validate: dateOrder('start_date', 'end_date') }]}
          onSubmit={async (p) => { if (editing.id) await api.patch(`${H}/performance/cycles/${editing.id}/`, p); else await api.post(`${H}/performance/cycles/`, p); notify('Cycle saved.'); setEditing(null); reload(); }} onClose={() => setEditing(null)} />
      )}
      {dialog}
    </section>
  );
}

function Summary({ id }) {
  const state = useData(`${H}/performance/cycles/${id}/summary/`);
  return (
    <section className="card" aria-label="Appraisal summary">
      <Gate state={state}>{(s) => (
        <>
          <h2>Appraisal summary: {s.cycle.name}</h2>
          <div className="tiles">
            <Tile label="Reviews completed" value={`${s.completed} of ${s.reviews}`} hint={`${s.waiting_self} waiting for self review, ${s.waiting_manager} for the manager`} />
            <Tile label="Average manager rating" value={s.manager_average ?? '—'} hint="Out of 5" /><Tile label="Average self rating" value={s.self_average ?? '—'} hint="Out of 5" />
            <Tile label="Goal progress" value={`${s.goals.average_progress}%`} hint={`${s.goals.achieved} of ${s.goals.total} goals achieved`} />
          </div>
          <div className="grid-2">
            <div><h3>Manager ratings</h3><HBars rows={s.distribution.slice().reverse().map((d) => ({ key: d.rating, label: `${d.rating} star${d.rating === 1 ? '' : 's'}`, value: d.count }))} format={(v) => String(v)} /></div>
            <div><h3>Average rating by department</h3><HBars rows={s.by_department.map((d) => ({ key: d.name, label: d.name, value: d.average }))} format={(v) => Number(v).toFixed(2)} empty="No completed reviews yet." /></div>
          </div>
        </>
      )}</Gate>
    </section>
  );
}

function Reviews({ cycle }) {
  const notify = useToast();
  const [dlg, setDlg] = useState(null);
  const state = useData(`${H}/performance/reviews/`, { cycle: cycle || undefined });
  const rows = asList(state.data);
  const rate = dlg?.kind === 'self';
  return (
    <section className="card" aria-label="Reviews">
      <h2>Reviews</h2>
      {state.loading ? <Loading /> : <ErrorBox error={state.error} onRetry={state.reload} />}
      {!state.loading && !state.error && rows.length === 0 && <p className="empty">No reviews yet. Reviews appear when a cycle is launched.</p>}
      {rows.length > 0 && (
        <div className="table-wrap"><table>
          <thead><tr><th>Employee</th><th>Cycle</th><th>Self</th><th>Manager</th><th>Status</th><th /></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.id}>
              <td data-label="Employee"><Link to={`/hr/employees/${r.employee.id}`}>{r.employee.name}</Link><div className="hint">{r.department}</div></td>
              <td data-label="Cycle">{r.cycle.name}</td><td data-label="Self">{r.self_rating ?? '—'}</td><td data-label="Manager">{r.manager_rating ?? '—'}</td>
              <td data-label="Status"><StatusBadge status={r.status} label={r.status_label} /></td>
              <td className="hr-row-actions">
                {r.can_self_review && <button type="button" className="btn btn-small btn-primary" onClick={() => setDlg({ kind: 'self', r })}>{r.self_rating ? 'Update self review' : 'Write self review'}</button>}
                {r.can_rate && <button type="button" className="btn btn-small btn-primary" onClick={() => setDlg({ kind: 'manager', r })}>{r.manager_rating ? 'Change rating' : 'Rate'}</button>}
                {(r.self_comments || r.manager_comments) && <button type="button" className="btn btn-small" onClick={() => setDlg({ kind: 'view', r })}>Comments</button>}
              </td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
      {dlg && dlg.kind !== 'view' && (
        <FormDialog title={`${rate ? 'Your review' : `Rate ${dlg.r.employee.name}`}, ${dlg.r.cycle.name}`} wide={false} submitLabel={rate ? 'Submit review' : 'Save rating'}
          record={rate ? { self_rating: dlg.r.self_rating ? String(dlg.r.self_rating) : '', self_comments: dlg.r.self_comments } : { manager_rating: dlg.r.manager_rating ? String(dlg.r.manager_rating) : '', manager_comments: dlg.r.manager_comments }}
          fields={rate ? [{ name: 'self_rating', label: 'How would you rate your work?', type: 'select', required: true, options: RATINGS }, { name: 'self_comments', label: 'What went well, and what would you improve?', type: 'textarea', wide: true, rows: 4, maxLength: 2000 }]
            : [{ name: 'manager_rating', label: 'Rating', type: 'select', required: true, options: RATINGS }, { name: 'manager_comments', label: 'Feedback', type: 'textarea', wide: true, rows: 4, maxLength: 2000 }]}
          onSubmit={async (p) => { await api.post(`${H}/performance/reviews/${dlg.r.id}/${dlg.kind}/`, p); notify('Saved.'); setDlg(null); state.reload(); }} onClose={() => setDlg(null)} />
      )}
      {dlg?.kind === 'view' && (
        <FormDialog title={`${dlg.r.employee.name}, ${dlg.r.cycle.name}`} wide={false} fields={[]} submitLabel="Close" onSubmit={async () => setDlg(null)} onClose={() => setDlg(null)}
          intro={<div><h3>Self review</h3><p>{dlg.r.self_comments || 'No comments.'}</p><h3>Manager feedback</h3><p>{dlg.r.manager_comments || 'No comments.'}</p></div>} />
      )}
    </section>
  );
}

function Goals({ cycle, cycles }) {
  const notify = useToast();
  const can = useCan();
  const [dlg, setDlg] = useState(null);
  const state = useData(`${H}/performance/goals/`, { cycle: cycle || undefined });
  const people = useData(`${H}/lookup/`, undefined, can('hr.manage') || can('hr.view'));
  const rows = asList(state.data);
  const open = cycles.filter((c) => c.status !== 'closed');
  const del = async (g) => { try { await api.del(`${H}/performance/goals/${g.id}/`); notify('Goal deleted.'); state.reload(); } catch (err) { notify(err.message, 'error'); } };
  return (
    <section className="card" aria-label="Goals">
      <div className="card-head"><h2>Goals</h2>{open.length > 0 && <button type="button" className="btn btn-primary" onClick={() => setDlg({ mode: 'new' })}>New goal</button>}</div>
      {state.loading ? <Loading /> : <ErrorBox error={state.error} onRetry={state.reload} />}
      {!state.loading && !state.error && rows.length === 0 && <p className="empty">No goals yet.</p>}
      {rows.length > 0 && (
        <div className="table-wrap"><table>
          <thead><tr><th>Goal</th><th>Employee</th><th>Cycle</th><th className="num">Weight</th><th>Progress</th><th>Status</th><th /></tr></thead>
          <tbody>{rows.map((g) => (
            <tr key={g.id}>
              <td data-label="Goal"><strong>{g.title}</strong>{g.description && <div className="hint">{g.description}</div>}{g.manager_comment && <div className="hint">Manager: {g.manager_comment}</div>}</td>
              <td data-label="Employee"><Link to={`/hr/employees/${g.employee.id}`}>{g.employee.name}</Link></td><td data-label="Cycle">{g.cycle.name}</td><td className="num" data-label="Weight">{g.weight}%</td>
              <td data-label="Progress"><span className="hr-progress" aria-hidden="true"><span style={{ width: `${g.progress}%` }} /></span>{g.progress}%</td><td data-label="Status"><StatusBadge status={g.status} label={g.status_label} /></td>
              <td className="hr-row-actions">
                {g.can_update_progress && <button type="button" className="btn btn-small" onClick={() => setDlg({ mode: 'edit', g })}>Update</button>}
                {g.can_manage && g.can_update_progress && <button type="button" className="btn btn-small btn-danger-outline" onClick={() => del(g)}>Delete</button>}
              </td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
      {dlg?.mode === 'new' && (
        <FormDialog title="New goal" submitLabel="Add goal" defaults={{ weight: '25', cycle: open[0]?.id }}
          fields={[...(can('hr.view') ? [{ name: 'employee', label: 'Employee', type: 'select', required: true, options: choose(options(asList(people.data), (p) => p.name)) }] : []),
            { name: 'cycle', label: 'Review cycle', type: 'select', required: true, options: choose(options(open)) }, { name: 'title', label: 'Goal', required: true, maxLength: 200, wide: true },
            { name: 'description', label: 'What success looks like', type: 'textarea', rows: 2, wide: true }, { name: 'weight', label: 'Weight (%)', type: 'number', min: 0, max: 100, integer: true, hint: 'The weights of one person\'s goals add up to at most 100.' }]}
          onSubmit={async (p) => { await api.post(`${H}/performance/goals/`, p); notify('Goal added.'); setDlg(null); state.reload(); }} onClose={() => setDlg(null)} />
      )}
      {dlg?.mode === 'edit' && (
        <FormDialog title={dlg.g.title} wide={false} record={dlg.g} submitLabel="Save"
          fields={[{ name: 'progress', label: 'Progress (%)', type: 'number', min: 0, max: 100, integer: true, required: true },
            { name: 'status', label: 'Status', type: 'select', required: true, options: [['not_started', 'Not started'], ['in_progress', 'In progress'], ['achieved', 'Achieved'], ['missed', 'Missed']].map(([value, label]) => ({ value, label })) },
            ...(dlg.g.can_manage ? [{ name: 'title', label: 'Goal', required: true, wide: true }, { name: 'weight', label: 'Weight (%)', type: 'number', min: 0, max: 100, integer: true }, { name: 'manager_comment', label: 'Manager comment', wide: true }] : [])]}
          onSubmit={async (p) => { await api.patch(`${H}/performance/goals/${dlg.g.id}/`, p); notify('Goal updated.'); setDlg(null); state.reload(); }} onClose={() => setDlg(null)} />
      )}
    </section>
  );
}

export default function Performance() {
  const can = useCan();
  const hr = can('hr.view');
  const [tab, setTab] = useState(hr ? 'cycles' : 'reviews');
  const [cycle, setCycle] = useState('');
  const cycles = useData(`${H}/performance/cycles/`, undefined, hr);
  const list = asList(cycles.data);
  useEffect(() => { if (!cycle && list.length) setCycle((list.find((c) => c.status === 'open') || list[0]).id); }, [list.length]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <>
      <PageHeader title="Performance" description="Review cycles, goals, and self and manager ratings." />
      <Tabs tabs={[...(hr ? [{ key: 'cycles', label: 'Cycles and summary' }] : []), { key: 'reviews', label: 'Reviews' }, { key: 'goals', label: 'Goals' }]} value={tab} onChange={setTab} />
      {hr && list.length > 0 && tab !== 'cycles' && (
        <div className="toolbar"><Field id="pf-cycle" label="Cycle"><select id="pf-cycle" value={cycle} onChange={(e) => setCycle(e.target.value)}><option value="">All cycles</option>{list.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field></div>
      )}
      {tab === 'cycles' && hr && (<>{can('hr.manage') ? <Cycles cycles={list} reload={cycles.reload} selected={cycle} onSelect={setCycle} /> : null}{cycle && <Summary id={cycle} />}</>)}
      {tab === 'reviews' && <Reviews cycle={hr ? cycle : ''} />}
      {tab === 'goals' && <Goals cycle={hr ? cycle : ''} cycles={list} />}
    </>
  );
}
