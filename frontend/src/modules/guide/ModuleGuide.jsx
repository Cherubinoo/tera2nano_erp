import { useEffect } from 'react';
import { Link, Navigate, useParams } from 'react-router-dom';
import Icon from '../../icons';
import { PageHeader } from '../../ui';
import { GuideList, ModuleLink, ScreenLink } from './common';
import { MODULE_GUIDES, RESERVED_KEYS, guideFor, nameOf, roleRows } from './guideData';

const SECTIONS = [
  ['used-for', 'What it is used for'], ['who', 'Who uses it'], ['screens', 'Key screens'], ['workflow', 'Step-by-step workflow'],
  ['statuses', 'Statuses'], ['connections', 'Connections to other modules'], ['automatic', 'What the system does by itself'],
  ['reports', 'Reports and exports'], ['limits', 'Good to know'],
];

function Statuses({ groups }) {
  if (!groups.length) return <p className="muted">This module has no statuses of its own: it only reads and presents figures.</p>;
  return groups.map((g) => (
    <div className="guide-status-group" key={g.title}>
      <h3>{g.title}</h3>
      <div className="table-wrap">
        <table className="guide-table">
          <thead><tr><th scope="col">Status</th><th scope="col">What it means</th></tr></thead>
          <tbody>{g.items.map(([name, meaning]) => (<tr key={name}><th scope="row" data-label="Status">{name}</th><td data-label="What it means">{meaning}</td></tr>))}</tbody>
        </table>
      </div>
    </div>
  ));
}

function FlowItems({ flows, side }) {
  if (!flows.length) return <p className="muted">Nothing is {side === 'out' ? 'sent to' : 'received from'} another module.</p>;
  return (
    <ul className="guide-connect">
      {flows.map((f) => (
        <li key={f.id}>
          <strong>{side === 'out' ? <>To <ModuleLink k={f.to} /></> : <>From <ModuleLink k={f.from} /></>}: {f.label}.</strong> {f.detail}
        </li>
      ))}
    </ul>
  );
}

export default function ModuleGuide() {
  const { moduleKey } = useParams();
  const g = guideFor(moduleKey);
  useEffect(() => { if (g) document.title = `${g.name} guide`; }, [g]);
  if (!g) return <Navigate to="/guide" replace />;
  const i = MODULE_GUIDES.indexOf(g);
  const prev = MODULE_GUIDES[i - 1];
  const next = MODULE_GUIDES[i + 1];
  const rows = roleRows(g.codes);

  return (
    <div className="guide-page" data-guide-module={g.key}>
      <GuideList current={g.name} />
      <div className="guide-body">
        <PageHeader title={g.name} description={g.purpose} actions={<span className="guide-head-ico" aria-hidden="true"><Icon name={g.icon} size={28} /></span>} />
        <nav className="guide-toc" aria-label="On this page">
          {SECTIONS.map(([id, label]) => <a key={id} href={`#${id}`} onClick={(e) => { e.preventDefault(); document.getElementById(id)?.scrollIntoView({ block: 'start' }); }}>{label}</a>)}
        </nav>

        <section className="card" id="used-for" aria-labelledby="h-used-for">
          <h2 id="h-used-for">What it is used for</h2>
          {g.usedFor.map((p) => <p key={p}>{p}</p>)}
        </section>

        <section className="card" id="who" aria-labelledby="h-who">
          <h2 id="h-who">Who uses it</h2>
          <p>{g.who}</p>
          <div className="table-wrap">
            <table className="guide-table">
              <thead><tr><th scope="col">Permission</th><th scope="col">What it allows</th><th scope="col">Roles that hold it by default</th></tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.code}>
                    <th scope="row" data-label="Permission"><code>{r.code}</code></th>
                    <td data-label="What it allows">{r.text}</td>
                    <td data-label="Roles">{r.roles.join(', ') || 'None'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="hint">These are the defaults. An administrator can change them in Users and Access; see <Link to="/guide/roles">Roles and access</Link>.</p>
        </section>

        <section className="card" id="screens" aria-labelledby="h-screens">
          <h2 id="h-screens">Key screens</h2>
          <ul className="guide-screens">
            {g.screens.map((s) => (
              <li key={s.to + s.label}>
                <ScreenLink to={s.to} className="guide-screen-link">{s.label}</ScreenLink>
                <span>{s.text}</span>
              </li>
            ))}
          </ul>
        </section>

        <section className="card" id="workflow" aria-labelledby="h-workflow">
          <h2 id="h-workflow">Step-by-step workflow</h2>
          <ol className="guide-workflow">
            {g.steps.map((s) => (
              <li key={s.title}>
                <h3>{s.title}</h3>
                <p>{s.text}</p>
                {s.example && <p className="guide-example"><strong>Example.</strong> {s.example}</p>}
              </li>
            ))}
          </ol>
        </section>

        <section className="card" id="statuses" aria-labelledby="h-statuses">
          <h2 id="h-statuses">Statuses and what each means</h2>
          <Statuses groups={g.statuses} />
        </section>

        <section className="card" id="connections" aria-labelledby="h-connections">
          <h2 id="h-connections">Connections to other modules</h2>
          <h3>Sends to other modules</h3>
          <FlowItems flows={g.sends} side="out" />
          <h3>Receives from other modules</h3>
          <FlowItems flows={g.receives} side="in" />
          <h3>Posting to the books</h3>
          <p>{g.ledger.text}</p>
          <h3>Approvals</h3>
          {g.approvals.length ? (
            <ul>{g.approvals.map((a) => <li key={a.kind}><strong>{a.kind}.</strong> {a.text}</li>)}</ul>
          ) : (
            <p>{g.key === 'approvals' ? 'This is the approvals module itself; see the steps above.' : 'This module does not send anything to Approvals.'}</p>
          )}
          <h3>Attachments</h3>
          <p>{g.attachments.length ? `Files can be attached to these records: ${g.attachments.join(', ')}.` : 'This module has no attachment boxes.'}</p>
          <p className="hint">Every change in this module is written to the change history (see <Link to="/guide/integration">How modules connect</Link>).</p>
        </section>

        <section className="card" id="automatic" aria-labelledby="h-automatic">
          <h2 id="h-automatic">What the system does by itself</h2>
          <ul>{g.automatic.map((t) => <li key={t}>{t}</li>)}</ul>
        </section>

        <section className="card" id="reports" aria-labelledby="h-reports">
          <h2 id="h-reports">Reports and exports</h2>
          {g.reports.map((t) => <p key={t}>{t}</p>)}
        </section>

        <section className="card" id="limits" aria-labelledby="h-limits">
          <h2 id="h-limits">Good to know</h2>
          <ul>{g.limits.map((t) => <li key={t}>{t}</li>)}</ul>
        </section>

        <nav className="guide-prevnext" aria-label="Other module guides">
          {prev ? <Link to={`/guide/${prev.key}`} rel="prev" className="guide-prev"><span className="muted">Previous</span><strong>{nameOf(prev.key)}</strong></Link> : <span />}
          {next ? <Link to={`/guide/${next.key}`} rel="next" className="guide-next"><span className="muted">Next</span><strong>{nameOf(next.key)}</strong></Link> : <span />}
        </nav>
      </div>
    </div>
  );
}

export const isReserved = (k) => RESERVED_KEYS.includes(k);
