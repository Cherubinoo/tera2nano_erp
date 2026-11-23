import { useState } from 'react';
import { api } from '../../api';
import { Badge, PageHeader, fmtDate, useConfirm, useToast } from '../../ui';
import { FormDialog, Gate, H, useCan, useData } from './common';

export default function Holidays() {
  const can = useCan();
  const notify = useToast();
  const { confirm, dialog } = useConfirm();
  const [year, setYear] = useState(new Date().getFullYear());
  const [editing, setEditing] = useState(null);
  const state = useData(`${H}/holidays/`, { year });
  const manage = can('hr.manage');
  const remove = async (h) => {
    if (!(await confirm({ title: `Delete ${h.name}?`, confirmLabel: 'Delete', danger: true }))) return;
    try { await api.del(`${H}/holidays/${h.id}/`); notify('Holiday deleted.'); state.reload(); } catch (err) { notify(err.message, 'error'); }
  };
  return (
    <>
      <PageHeader title="Holiday calendar" description="Company holidays do not use up leave and are not counted as absences."
        actions={manage && <button type="button" className="btn btn-primary" onClick={() => setEditing({})}>New holiday</button>} />
      <section className="card">
        <div className="toolbar">
          <button type="button" className="btn" onClick={() => setYear(year - 1)} aria-label="Previous year">←</button>
          <strong aria-live="polite">{year}</strong>
          <button type="button" className="btn" onClick={() => setYear(year + 1)} aria-label="Next year">→</button>
        </div>
        <Gate state={state}>{(rows) => (rows.length === 0 ? <p className="empty">No holidays are listed for {year}.</p> : (
          <div className="table-wrap"><table>
            <thead><tr><th>Date</th><th>Day</th><th>Holiday</th><th>Kind</th>{manage && <th />}</tr></thead>
            <tbody>{rows.map((h) => (
              <tr key={h.id}>
                <td data-label="Date">{fmtDate(h.date)}</td><td data-label="Day">{h.weekday}</td><td data-label="Holiday"><strong>{h.name}</strong></td>
                <td data-label="Kind">{h.is_optional ? <Badge tone="info">Optional</Badge> : <Badge tone="good">Company holiday</Badge>}</td>
                {manage && <td className="hr-row-actions"><button type="button" className="btn btn-small" onClick={() => setEditing(h)}>Edit</button><button type="button" className="btn btn-small btn-danger-outline" onClick={() => remove(h)}>Delete</button></td>}
              </tr>
            ))}</tbody>
          </table></div>
        ))}</Gate>
        {state.data?.length > 0 && <div className="list-foot"><span>{state.data.filter((h) => !h.is_optional).length} company holidays</span></div>}
      </section>
      {editing && (
        <FormDialog title={editing.id ? `Edit ${editing.name}` : 'New holiday'} wide={false} record={editing.id ? editing : undefined}
          defaults={{ date: `${year}-01-01` }}
          fields={[{ name: 'date', label: 'Date', type: 'date', required: true }, { name: 'name', label: 'Holiday name', required: true, maxLength: 120 },
            { name: 'is_optional', label: 'Optional (people may choose to take it)', type: 'checkbox' }]}
          onSubmit={async (p) => {
            if (editing.id) await api.patch(`${H}/holidays/${editing.id}/`, p); else await api.post(`${H}/holidays/`, p);
            notify('Holiday saved.'); setEditing(null); state.reload();
          }} onClose={() => setEditing(null)} />
      )}
      {dialog}
    </>
  );
}
