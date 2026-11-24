import { useEffect, useMemo, useState } from 'react';
import { Link, Navigate, useLocation, useParams } from 'react-router-dom';
import { PageHeader } from '../../ui';
import { GuideList, ScreenLink } from './common';
import { OPERATE_CHAPTERS, OPERATE_INTRO, chapterFor } from './operateData';

const slug = (t) => t.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

function Where({ task }) {
  if (!task.where && !task.route) return null;
  return (
    <p className="op-where">
      <strong>Where: </strong>
      {task.route ? <ScreenLink to={task.route}>{task.where || task.route}</ScreenLink> : task.where}
    </p>
  );
}

function Task({ task, id }) {
  return (
    <article className="card op-task" id={id} aria-labelledby={`${id}-h`}>
      <h3 id={`${id}-h`}>{task.title}</h3>
      {task.who && <p className="op-who"><strong>Who: </strong>{task.who}</p>}
      <Where task={task} />
      <ol className="op-steps">{task.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
      {task.result && <p className="op-result"><strong>What happens next: </strong>{task.result}</p>}
      {task.tips?.length > 0 && (
        <div className="op-tips">
          <strong>Tips</strong>
          <ul>{task.tips.map((t, i) => <li key={i}>{t}</li>)}</ul>
        </div>
      )}
    </article>
  );
}

const ROUTINES = [['daily', 'Every day'], ['weekly', 'Every week'], ['monthly', 'Every month'], ['yearly', 'Every year']];

function Chapter({ c }) {
  const i = OPERATE_CHAPTERS.indexOf(c);
  const prev = OPERATE_CHAPTERS[i - 1];
  const next = OPERATE_CHAPTERS[i + 1];
  const routines = ROUTINES.filter(([k]) => c.routines?.[k]?.length);
  const { hash } = useLocation();
  useEffect(() => {
    document.title = `How to operate: ${c.title}`;
    const el = hash ? document.getElementById(decodeURIComponent(hash.slice(1))) : null;
    if (el) el.scrollIntoView({ block: 'start' }); else window.scrollTo(0, 0);
  }, [c, hash]);
  return (
    <div className="guide-page op-page">
      <GuideList current={`How to operate: ${c.title}`} />
      <div className="guide-body">
        <PageHeader title={`How to operate: ${c.title}`} description={c.purpose} />
        <nav className="guide-toc" aria-label="Tasks in this chapter">
          {c.tasks.map((t) => (
            <a key={t.title} href={`#t-${slug(t.title)}`} onClick={(e) => { e.preventDefault(); document.getElementById(`t-${slug(t.title)}`)?.scrollIntoView({ block: 'start' }); }}>{t.title}</a>
          ))}
        </nav>

        {c.who?.length > 0 && (
          <section className="card" aria-labelledby="op-who-h">
            <h2 id="op-who-h">Who uses it</h2>
            <div className="table-wrap">
              <table className="guide-table">
                <thead><tr><th scope="col">Role</th><th scope="col">What they do here</th></tr></thead>
                <tbody>{c.who.map((w) => <tr key={w.role}><th scope="row" data-label="Role">{w.role}</th><td data-label="What they do">{w.does}</td></tr>)}</tbody>
              </table>
            </div>
          </section>
        )}

        {c.before_you_start?.length > 0 && (
          <section className="card" aria-labelledby="op-before-h">
            <h2 id="op-before-h">Before you start</h2>
            <ul>{c.before_you_start.map((b, k) => <li key={k}>{b}</li>)}</ul>
          </section>
        )}

        <h2 className="op-section-title">Tasks</h2>
        {c.tasks.map((t) => <Task key={t.title} task={t} id={`t-${slug(t.title)}`} />)}

        {c.statuses?.length > 0 && (
          <section className="card" aria-labelledby="op-status-h">
            <h2 id="op-status-h">Statuses</h2>
            <div className="table-wrap">
              <table className="guide-table">
                <thead><tr><th scope="col">Status</th><th scope="col">What it means</th></tr></thead>
                <tbody>{c.statuses.map((s) => <tr key={s.name}><th scope="row" data-label="Status">{s.name}</th><td data-label="What it means">{s.meaning}</td></tr>)}</tbody>
              </table>
            </div>
          </section>
        )}

        {routines.length > 0 && (
          <section className="card" aria-labelledby="op-routine-h">
            <h2 id="op-routine-h">Routines</h2>
            <div className="op-routines">
              {routines.map(([k, label]) => (
                <div key={k}><h3>{label}</h3><ul>{c.routines[k].map((r, n) => <li key={n}>{r}</li>)}</ul></div>
              ))}
            </div>
          </section>
        )}

        {c.common_problems?.length > 0 && (
          <section className="card" aria-labelledby="op-prob-h">
            <h2 id="op-prob-h">Common problems</h2>
            <div className="table-wrap">
              <table className="guide-table">
                <thead><tr><th scope="col">Problem</th><th scope="col">What to do</th></tr></thead>
                <tbody>{c.common_problems.map((p, n) => <tr key={n}><td data-label="Problem">{p.problem}</td><td data-label="What to do">{p.fix}</td></tr>)}</tbody>
              </table>
            </div>
          </section>
        )}

        {c.connected?.length > 0 && (
          <section className="card" aria-labelledby="op-conn-h">
            <h2 id="op-conn-h">Connections to other modules</h2>
            <ul>{c.connected.map((x, n) => <li key={n}>{x}</li>)}</ul>
          </section>
        )}

        <nav className="guide-prevnext" aria-label="Previous and next chapter">
          {prev
            ? <Link to={`/guide/operate/${prev.key}`} rel="prev" className="guide-prev"><span className="muted">Previous</span><strong>{prev.title}</strong></Link>
            : <Link to="/guide/operate" className="guide-prev"><span className="muted">Back to</span><strong>All chapters</strong></Link>}
          {next ? <Link to={`/guide/operate/${next.key}`} rel="next" className="guide-next"><span className="muted">Next</span><strong>{next.title}</strong></Link> : <span />}
        </nav>
      </div>
    </div>
  );
}

function Index() {
  const [q, setQ] = useState('');
  useEffect(() => { document.title = 'How to operate'; }, []);
  const hits = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (s.length < 2) return [];
    return OPERATE_CHAPTERS.flatMap((c) => c.tasks
      .filter((t) => `${t.title} ${t.where}`.toLowerCase().includes(s))
      .map((t) => ({ c, t })));
  }, [q]);
  return (
    <div className="guide-page op-page">
      <GuideList current="How to operate" />
      <div className="guide-body">
        <PageHeader title={OPERATE_INTRO.title} description={OPERATE_INTRO.lead} />
        <section className="card" aria-labelledby="op-use-h">
          <h2 id="op-use-h">How to use this manual</h2>
          <ul>{OPERATE_INTRO.howToUse.map((h, k) => <li key={k}>{h}</li>)}</ul>
          <label className="op-search">
            <span>Find a task</span>
            <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. invoice, payroll, invite" />
          </label>
          {q.trim().length >= 2 && (
            hits.length
              ? <ul className="op-hits">{hits.slice(0, 40).map(({ c, t }) => (
                <li key={`${c.key}-${t.title}`}><Link to={`/guide/operate/${c.key}#t-${slug(t.title)}`}>{t.title}</Link> <span className="muted">· {c.title}</span></li>
              ))}</ul>
              : <p className="muted">No task matches “{q}”.</p>
          )}
        </section>
        <div className="op-grid">
          {OPERATE_CHAPTERS.map((c) => (
            <Link key={c.key} to={`/guide/operate/${c.key}`} className="card op-card">
              <h2>{c.title}</h2>
              <p>{c.purpose}</p>
              <span className="muted">{c.tasks.length} tasks</span>
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}

export default function Operate() {
  const { chapter } = useParams();
  if (!chapter) return <Index />;
  const c = chapterFor(chapter);
  if (!c) return <Navigate to="/guide/operate" replace />;
  return <Chapter c={c} />;
}
