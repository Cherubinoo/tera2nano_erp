import { useEffect } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { PageHeader } from '../../ui';
import { GuideList, ModuleLink, Yes } from './common';
import { CROSS_CUTTING, FLOWS, MODULE_GUIDES, nameOf } from './guideData';

// Where each module sits in the map (a 1270 x 690 canvas). Row 1 modules post into the Accounting bar below them and send requests to
// the Approvals bar above them; GST & Tax, Compliance and Reports sit under the books; the foundation modules are along the bottom.
const NODES = [
  { key: 'approvals', x: 6, y: 24, w: 1258, h: 46, bar: true, sub: 'Quotations, orders, bills, payments, expenses, leave, payroll, salary revisions, budgets and withdrawals wait here' },
  { key: 'crm', x: 62, y: 160, w: 126, h: 50, sub: 'leads and deals' },
  { key: 'sales', x: 276, y: 160, w: 126, h: 50, sub: 'invoices, receipts' },
  { key: 'projects', x: 490, y: 160, w: 126, h: 50, sub: 'tasks, profit' },
  { key: 'purchases', x: 704, y: 160, w: 126, h: 50, sub: 'bills, payments' },
  { key: 'hr', x: 918, y: 160, w: 126, h: 50, sub: 'payroll' },
  { key: 'partners', x: 1132, y: 160, w: 126, h: 50, sub: 'capital, profit share' },
  { key: 'accounting', x: 6, y: 340, w: 1258, h: 60, bar: true, ledger: true, sub: 'The books: every posting lands here as a balanced journal entry; ledgers and statements are read from it' },
  { key: 'gst-tax', x: 110, y: 490, w: 200, h: 50, sub: 'registers, returns, TDS' },
  { key: 'compliance', x: 410, y: 490, w: 200, h: 50, sub: 'filings and due dates' },
  { key: 'reports', x: 760, y: 490, w: 250, h: 50, sub: 'also reads every module, read only' },
  { key: 'company', x: 6, y: 612, w: 410, h: 62, found: true, sub: ['profile, numbering, letterhead, tax and bank', 'settings feed every document'] },
  { key: 'admin', x: 430, y: 612, w: 410, h: 62, found: true, sub: ['users, roles and permissions; one change', 'history for every module'] },
  { key: 'documents', x: 854, y: 612, w: 410, h: 62, found: true, sub: ['issued PDFs, letters, uploaded records,', 'attachments'] },
];

// Arrows, keyed by the id of the flow they draw (see FLOWS). `both` = a request goes out and a decision comes back.
const EDGES = {
  'crm-sales-customer': { d: 'M190 185H272', label: { x: 231, y: 232, a: 'middle', t: ['won deal creates', 'customer'] } },
  'projects-sales-milestone': { d: 'M488 185H406', label: { x: 447, y: 232, a: 'middle', t: ['milestone creates', 'draft invoice'] } },
  'sales-accounting': { d: 'M339 212V336', label: { x: 349, y: 276, a: 'start', t: ['invoice and receipt', 'post journal'] } },
  'purchases-accounting': { d: 'M767 212V336', label: { x: 777, y: 250, a: 'start', t: ['bill and payment', 'post journal'] } },
  'hr-accounting': { d: 'M981 212V336', label: { x: 991, y: 304, a: 'start', t: ['payroll posts', 'journal'] } },
  'partners-accounting': { d: 'M1195 212V336', label: { x: 1185, y: 250, a: 'end', t: ['capital, drawings', 'and profit share', 'post journal'] } },
  'accounting-projects-tag': { d: 'M553 338V214', label: { x: 563, y: 270, a: 'start', t: ['project tag on', 'journal lines'] } },
  'accounting-tax': { d: 'M180 402V486', label: { x: 170, y: 436, a: 'end', t: ['ledger lines feed', 'GST and TDS'] } },
  'tax-accounting': { d: 'M240 488V404', label: { x: 250, y: 436, a: 'start', t: ['challans and set-off', 'post journal'] } },
  'tax-compliance': { d: 'M312 515H406', label: { x: 359, y: 560, a: 'middle', t: ['tax due dates,', 'read only'] } },
  'accounting-reports': { d: 'M885 402V486', label: { x: 895, y: 436, a: 'start', t: ['books feed', 'financial reports'] } },
  'sales-approvals': { d: 'M339 158V72', both: true, label: { x: 349, y: 108, a: 'start', t: ['quotation,', 'payment void'] } },
  'projects-approvals': { d: 'M553 158V72', both: true, label: { x: 563, y: 108, a: 'start', t: ['budget', 'change'] } },
  'purchases-approvals': { d: 'M767 158V72', both: true, label: { x: 777, y: 108, a: 'start', t: ['order, bill,', 'payment'] } },
  'hr-approvals': { d: 'M981 158V72', both: true, label: { x: 991, y: 108, a: 'start', t: ['leave, payroll,', 'salary revision'] } },
  'partners-approvals': { d: 'M1195 158V72', both: true, label: { x: 1185, y: 108, a: 'end', t: ['withdrawal'] } },
  'accounting-approvals': { d: 'M30 338V72', both: true, label: { x: 24, y: 206, a: 'middle', t: ['expense'], rotate: true } },
};

function SvgLink({ to, label, children }) {
  const navigate = useNavigate();
  const onClick = (e) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    navigate(to);
  };
  return <a href={to} onClick={onClick} aria-label={label} className="guide-map-link">{children}</a>;
}

function Lines({ x, y, a, t, cls, step = 14, rotate }) {
  return (
    <text className={cls} x={x} y={y} textAnchor={a} transform={rotate ? `rotate(-90 ${x} ${y})` : undefined}>
      {t.map((line, i) => <tspan key={line} x={x} dy={i === 0 ? 0 : step}>{line}</tspan>)}
    </text>
  );
}

function Map() {
  const drawn = FLOWS.filter((f) => EDGES[f.id]);
  return (
    <div className="guide-map-wrap">
      <svg className="guide-map" viewBox="0 0 1270 690" role="img" aria-labelledby="guide-map-title guide-map-desc" data-testid="integration-map">
        <title id="guide-map-title">Integration map of the modules</title>
        <desc id="guide-map-desc">
          Modules are boxes and arrows show what flows between them. Sales, Purchases, People and Partners post journal entries into Accounting.
          Accounting feeds GST and Tax and Reports, and its project tags feed Projects. GST and Tax posts challans and set-off back to Accounting
          and its due dates are read by Compliance. CRM creates Sales customers, and Projects creates draft invoices in Sales. Sales, Purchases,
          Accounting, People, Partners and Projects send requests to Approvals and receive its decision. The same flows are listed as text below the diagram.
        </desc>
        <defs>
          <marker id="guide-ah" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse" orient="auto-start-reverse">
            <path d="M0 0L10 5L0 10z" className="guide-ah" />
          </marker>
        </defs>

        {drawn.map((f) => {
          const e = EDGES[f.id];
          return (
            <g key={f.id} className="guide-edge" data-flow={f.id}>
              <title>{`${nameOf(f.from)} to ${nameOf(f.to)}: ${f.label}. ${f.detail}`}</title>
              <path d={e.d} markerEnd="url(#guide-ah)" markerStart={e.both ? 'url(#guide-ah)' : undefined} />
              <Lines {...e.label} cls="guide-edge-label" />
            </g>
          );
        })}

        {NODES.map((n) => (
          <SvgLink key={n.key} to={`/guide/${n.key}`} label={`${nameOf(n.key)} guide`}>
            <g className={`guide-mnode${n.bar ? ' is-bar' : ''}${n.ledger ? ' is-ledger' : ''}${n.found ? ' is-found' : ''}`} data-node={n.key}>
              <rect x={n.x} y={n.y} width={n.w} height={n.h} rx="7" />
              {n.bar || n.found ? (
                <>
                  <text className="guide-mnode-name" x={n.x + 14} y={n.y + 19}>{nameOf(n.key)}</text>
                  <text className="guide-mnode-sub" x={n.x + 14} y={n.y + 37}>{[].concat(n.sub).map((line, i) => <tspan key={line} x={n.x + 14} dy={i === 0 ? 0 : 15}>{line}</tspan>)}</text>
                </>
              ) : (
                <>
                  <text className="guide-mnode-name" x={n.x + n.w / 2} y={n.y + 22} textAnchor="middle">{nameOf(n.key)}</text>
                  <text className="guide-mnode-sub" x={n.x + n.w / 2} y={n.y + 38} textAnchor="middle">{n.sub}</text>
                </>
              )}
            </g>
          </SvgLink>
        ))}
        <text className="guide-edge-label" x="6" y="602">Foundation: used by every module</text>
      </svg>
    </div>
  );
}

function Matrix() {
  return (
    <div className="table-wrap">
      <table className="guide-matrix">
        <thead>
          <tr>
            <th scope="col">Module</th><th scope="col">Sends data to</th><th scope="col">Receives data from</th>
            <th scope="col">Posts to the ledger?</th><th scope="col">Uses approvals?</th><th scope="col">Uses attachments?</th><th scope="col">Permission codes</th>
          </tr>
        </thead>
        <tbody>
          {MODULE_GUIDES.map((g) => (
            <tr key={g.key} data-module={g.key}>
              <th scope="row" data-label="Module"><Link to={`/guide/${g.key}`}>{g.name}</Link></th>
              <td data-label="Sends data to">{g.sendsTo.length ? g.sendsTo.map((k, i) => <span key={k}>{i > 0 && ', '}<ModuleLink k={k} /></span>) : <span className="muted">{g.matrixNote?.sends || '—'}</span>}</td>
              <td data-label="Receives data from">{g.receivesFrom.length ? g.receivesFrom.map((k, i) => <span key={k}>{i > 0 && ', '}<ModuleLink k={k} /></span>) : <span className="muted">{g.matrixNote?.receives || '—'}</span>}</td>
              <td data-label="Posts to the ledger?"><Yes on={g.ledger.posts}>{g.ledger.posts ? (g.key === 'accounting' ? 'Yes (it is the ledger)' : 'Yes') : 'No'}</Yes></td>
              <td data-label="Uses approvals?">{g.approvals.length ? <><Yes on>Yes</Yes>: {g.approvals.map((a) => a.kind).join(', ')}</> : <Yes on={false}>No</Yes>}</td>
              <td data-label="Uses attachments?">{g.attachments.length ? <><Yes on>Yes</Yes>: {g.attachments.join(', ')}</> : <Yes on={false}>No</Yes>}</td>
              <td data-label="Permission codes"><code>{g.codes.join(' ')}</code></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function Integration() {
  useEffect(() => { document.title = 'How modules connect'; }, []);
  const byFrom = MODULE_GUIDES.map((g) => ({ g, flows: FLOWS.filter((f) => f.from === g.key) })).filter((x) => x.flows.length);
  return (
    <div className="guide-page">
      <GuideList current="How modules connect" />
      <div className="guide-body">
        <PageHeader title="How modules connect" description="What flows between the modules, which ones post to the books, and which use approvals and attachments." />

        <section className="card" aria-labelledby="map-h">
          <div className="card-head"><h2 id="map-h">Integration map</h2></div>
          <p className="muted">Click a module to read its guide. Arrows point the way the data goes; double-headed arrows to Approvals are a request out and a decision back.</p>
          <Map />
          <p className="guide-map-phone hint">The map is wide, so on a phone read the same flows in the list below.</p>
        </section>

        <section className="card" aria-labelledby="flows-h">
          <h2 id="flows-h">The same flows as a list</h2>
          <p className="muted">This is the text alternative to the map, and it also includes flows that would crowd the picture.</p>
          {byFrom.map(({ g, flows }) => (
            <div className="guide-flowlist" key={g.key}>
              <h3><Link to={`/guide/${g.key}`}>{g.name}</Link> sends</h3>
              <ul>
                {flows.map((f) => (
                  <li key={f.id} data-flow-item={f.id}><strong>{g.name} to {nameOf(f.to)}: {f.label}.</strong> {f.detail}</li>
                ))}
              </ul>
            </div>
          ))}
        </section>

        <section className="card" aria-labelledby="matrix-h">
          <h2 id="matrix-h">Module by module</h2>
          <p className="muted">Each row reads from the same list of flows as the map, so they cannot disagree. Permission codes are the ones that matter for the module; see Roles and access for who holds them.</p>
          <Matrix />
        </section>

        <section className="card" aria-labelledby="cross-h">
          <h2 id="cross-h">Across all modules</h2>
          <dl className="guide-cross">
            {CROSS_CUTTING.map((c) => (<div key={c.title}><dt>{c.title}</dt><dd>{c.text}</dd></div>))}
          </dl>
        </section>
      </div>
    </div>
  );
}
