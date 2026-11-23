import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { Field, PageHeader } from '../../ui';
import { GuideList, ScreenLink } from './common';
import { GLOSSARY, MATRIX, PERMISSIONS, ROLES, ROLE_NOTES } from './guideData';

const GROUPS = [...new Set(PERMISSIONS.map((p) => p.group))];
const holds = (role, code) => (MATRIX[role] || []).includes(code);

export function Roles() {
  useEffect(() => { document.title = 'Roles and access'; }, []);
  return (
    <div className="guide-page">
      <GuideList current="Roles and access" />
      <div className="guide-body">
        <PageHeader title="Roles and access" description="Seven roles, and what each can do by default." />
        <section className="card" aria-labelledby="roles-h">
          <h2 id="roles-h">The roles</h2>
          <dl className="guide-roles">
            {ROLES.map((r) => (
              <div key={r.value}><dt>{r.label}</dt><dd>{ROLE_NOTES[r.value]} <span className="muted">({(MATRIX[r.value] || []).length} of {PERMISSIONS.length} permissions)</span></dd></div>
            ))}
          </dl>
          <p className="hint">These are the defaults from the permission matrix in the server. An administrator can change any role except Admin in <ScreenLink to="/company/access">Users and Access</ScreenLink> (each change needs a reason and is logged), so check that screen for what is in force today. Anyone signed in can also use their own pages, such as My Leave and My Payslips.</p>
        </section>

        <section className="card" aria-labelledby="matrix-h" data-testid="roles-table">
          <h2 id="matrix-h">What each role can do</h2>
          <div className="table-wrap guide-roles-wide">
            <table className="guide-matrix guide-roles-table">
              <thead>
                <tr><th scope="col">Permission</th>{ROLES.map((r) => <th scope="col" key={r.value} className="guide-role-col">{r.label}</th>)}</tr>
              </thead>
              {GROUPS.map((group) => (
                <tbody key={group}>
                  <tr className="guide-group-row"><th scope="rowgroup" colSpan={ROLES.length + 1}>{group}</th></tr>
                  {PERMISSIONS.filter((p) => p.group === group).map((p) => (
                    <tr key={p.code}>
                      <th scope="row"><code>{p.code}</code><span className="guide-perm-text">{p.text}</span></th>
                      {ROLES.map((r) => (
                        <td key={r.value} className="guide-cell">{holds(r.value, p.code) ? <span className="guide-yes" aria-label="Yes">✓</span> : <span className="guide-no" aria-label="No">–</span>}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              ))}
            </table>
          </div>
          <div className="guide-roles-narrow">
            {ROLES.map((r) => (
              <details key={r.value}>
                <summary>{r.label} <span className="muted">({(MATRIX[r.value] || []).length} permissions)</span></summary>
                <ul>{PERMISSIONS.filter((p) => holds(r.value, p.code)).map((p) => <li key={p.code}><code>{p.code}</code> {p.text}</li>)}</ul>
              </details>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

export function Glossary() {
  const [q, setQ] = useState('');
  useEffect(() => { document.title = 'Glossary'; }, []);
  const term = q.trim().toLowerCase();
  const rows = useMemo(() => GLOSSARY.filter((g) => !term || `${g.term} ${g.meaning}`.toLowerCase().includes(term)), [term]);
  return (
    <div className="guide-page">
      <GuideList current="Glossary" />
      <div className="guide-body">
        <PageHeader title="Glossary" description="The Indian tax, LLP, payroll and accounting terms used in the system." />
        <section className="card" aria-labelledby="gl-h">
          <h2 id="gl-h" className="sr-only">Terms</h2>
          <Field id="gl-search" label="Find a term" hint="Type part of a term or its meaning.">
            <input id="gl-search" type="search" value={q} onChange={(e) => setQ(e.target.value)} autoComplete="off" />
          </Field>
          {rows.length === 0 && <p className="empty">No term matches &ldquo;{q}&rdquo;.</p>}
          <dl className="guide-glossary">
            {rows.map((g) => (<div key={g.term}><dt>{g.term}</dt><dd>{g.meaning}</dd></div>))}
          </dl>
          <p className="hint">These explain how the terms are used in this system; they are not tax or legal advice. Rates, forms and due dates change, so confirm them with an accountant and the current notifications. See also <Link to="/guide/gst-tax">GST & Tax</Link>.</p>
        </section>
      </div>
    </div>
  );
}
