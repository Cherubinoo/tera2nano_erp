import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '../../api';
import { today } from '../../format';
import { useLoad } from '../../hooks';
import { Badge, Dialog, ErrorBox, Field, FormErrors, Loading, PageHeader, Tabs, fmtDate, useConfirm, useToast } from '../../ui';
import { AudienceFields, AudienceText, Markdown, useCan } from './common';

const STATE_TONE = { live: 'good', scheduled: 'info', expired: 'neutral' };

function AnnouncementDialog({ item, onClose, onSaved }) {
  const notify = useToast();
  const [v, setV] = useState({
    title: item?.title || '', body: item?.body || '', audience: item?.audience || 'all', roles: item?.roles || [],
    pinned: item?.pinned || false, publish_on: item?.publish_on || today(), expires_on: item?.expires_on || '',
  });
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => { setV((x) => ({ ...x, [k]: e.target.value })); setErrors((x) => ({ ...x, [k]: undefined })); };

  const submit = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    setFailure(null);
    const errs = {};
    if (!v.title.trim()) errs.title = 'Enter the title.';
    if (!v.body.trim()) errs.body = 'Enter the message.';
    if (v.audience === 'roles' && v.roles.length === 0) errs.roles = 'Choose at least one role.';
    if (v.expires_on && v.publish_on && v.expires_on < v.publish_on) errs.expires_on = 'The expiry date cannot be before the publish date.';
    setErrors(errs);
    if (Object.keys(errs).length) return;
    setBusy(true);
    try {
      const body = { ...v, title: v.title.trim(), expires_on: v.expires_on || null };
      if (item) await api.patch(`/workplace/announcements/${item.id}/`, body); else await api.post('/workplace/announcements/', body);
      notify(item ? 'Announcement saved.' : 'Announcement published.');
      onSaved();
    } catch (err) { setFailure(err); setErrors(err.fieldErrors || {}); }
    setBusy(false);
  };
  return (
    <Dialog title={item ? 'Edit announcement' : 'New announcement'} onClose={onClose} wide>
      <form onSubmit={submit} noValidate>
        <FormErrors error={failure} fields={[{ name: 'title' }, { name: 'body' }, { name: 'audience' }, { name: 'roles' }, { name: 'publish_on' }, { name: 'expires_on' }]} />
        <div className="form-grid">
          <Field id="an-title" label="Title" required wide error={errors.title}><input id="an-title" value={v.title} maxLength={200} onChange={set('title')} /></Field>
          <Field id="an-body" label="Message" required wide error={errors.body} hint="You can use Markdown: **bold**, *italic*, lists with - and links."><textarea id="an-body" rows={6} value={v.body} onChange={set('body')} /></Field>
          <AudienceFields prefix="an" audience={v.audience} roles={v.roles} error={errors} onChange={(c) => setV((x) => ({ ...x, ...c }))} />
          <Field id="an-pub" label="Publish on" error={errors.publish_on}><input id="an-pub" type="date" value={v.publish_on} onChange={set('publish_on')} /></Field>
          <Field id="an-exp" label="Expires on" hint="Optional" error={errors.expires_on}><input id="an-exp" type="date" value={v.expires_on} onChange={set('expires_on')} /></Field>
          <label className="check field-wide"><input type="checkbox" checked={v.pinned} onChange={(e) => setV((x) => ({ ...x, pinned: e.target.checked }))} /> Pin to the top of the feed</label>
        </div>
        {v.body.trim() && <fieldset className="subcard"><legend>Preview</legend><Markdown text={v.body} /></fieldset>}
        <div className="dialog-foot-inline">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : item ? 'Save' : 'Publish'}</button>
        </div>
      </form>
    </Dialog>
  );
}

export default function AnnouncementsPage() {
  const can = useCan();
  const notify = useToast();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const [params, setParams] = useSearchParams();
  const manager = can('workplace.manage');
  const [tab, setTab] = useState('feed');
  const [dialog, setDialog] = useState(params.get('new') === '1' && manager ? {} : null);
  useEffect(() => { if (params.get('new')) setParams({}, { replace: true }); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const { data, error, loading, reload } = useLoad(() => api.get('/workplace/announcements/', tab === 'manage' ? { all: 1 } : {}), [tab]);
  const rows = data || [];

  const remove = async (a) => {
    if (!(await confirm({ title: `Delete "${a.title}"?`, body: <p>It disappears from everyone's feed. This is recorded in the change history.</p>, confirmLabel: 'Delete', danger: true }))) return;
    try { await api.del(`/workplace/announcements/${a.id}/`); notify('Announcement deleted.'); reload(); } catch (err) { notify(err.message, 'error'); }
  };

  return (
    <>
      <PageHeader title="Announcements" description="News and notices from the management."
        actions={manager && <button type="button" className="btn btn-primary" onClick={() => setDialog({})}>New announcement</button>} />
      {manager && <Tabs tabs={[{ key: 'feed', label: 'Feed' }, { key: 'manage', label: 'Manage all' }]} value={tab} onChange={setTab} />}
      {loading && <Loading />}
      <ErrorBox error={error} onRetry={reload} />
      {!loading && !error && rows.length === 0 && <section className="card"><p className="empty">No announcements.</p></section>}

      {tab === 'feed' && rows.map((a) => (
        <article key={a.id} className="card wp-ann" aria-label={a.title}>
          <h2>{a.title} {a.pinned && <Badge tone="info">Pinned</Badge>}</h2>
          <p className="hint">{fmtDate(a.publish_on)}{a.created_by ? ` · ${a.created_by.name}` : ''}{a.audience === 'roles' ? ` · for ${a.role_labels.join(', ')}` : ''}</p>
          <Markdown text={a.body} />
        </article>
      ))}

      {tab === 'manage' && rows.length > 0 && (
        <section className="card">
          <div className="table-wrap"><table>
            <thead><tr><th>Title</th><th>Audience</th><th>Publish</th><th>Expires</th><th>State</th><th><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {rows.map((a) => (
                <tr key={a.id}>
                  <td data-label="Title"><strong>{a.title}</strong> {a.pinned && <Badge tone="info">Pinned</Badge>}</td>
                  <td data-label="Audience"><AudienceText row={a} /></td>
                  <td data-label="Publish">{fmtDate(a.publish_on)}</td>
                  <td data-label="Expires">{fmtDate(a.expires_on)}</td>
                  <td data-label="State"><Badge tone={STATE_TONE[a.state]}>{a.state}</Badge></td>
                  <td><div className="row-actions">
                    <button type="button" className="btn btn-small" onClick={() => setDialog(a)}>Edit</button>
                    <button type="button" className="btn btn-small btn-danger-outline" onClick={() => remove(a)}>Delete</button>
                  </div></td>
                </tr>
              ))}
            </tbody>
          </table></div>
        </section>
      )}
      {dialog && <AnnouncementDialog item={dialog.id ? dialog : null} onClose={() => setDialog(null)} onSaved={() => { setDialog(null); reload(); }} />}
      {confirmDialog}
    </>
  );
}
