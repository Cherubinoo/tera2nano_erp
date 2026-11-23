import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import Icon from '../../icons';
import { MODULE_META } from '../../moduleMeta';
import { PageHeader } from '../../ui';
import { ScreenLink, useCanOpen, useReducedMotion } from './common';
import { FLOW_LANES, FLOW_STEPS, MODULE_GUIDES, OVERVIEW } from './guideData';

const STEPS = FLOW_STEPS.map((s, i) => ({ ...s, n: i + 1 }));
const STEP_MS = 5200;

function Arrow() {
  return (
    <span className="guide-arrow" aria-hidden="true">
      <svg viewBox="0 0 28 14" width="28" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M1 7h22M18 2l6 5-6 5" /></svg>
    </span>
  );
}

// One step of the diagram: a link to the real screen (or to the module's guide page when the person's role cannot open it).
function Node({ step, active }) {
  const canOpen = useCanOpen();
  const meta = MODULE_META[step.module];
  const to = canOpen(step.to) ? step.to : `/guide/${step.module}`;
  return (
    <Link to={to} className={`guide-node${active ? ' is-active' : ''}`} data-step={step.n} data-step-id={step.id}
      aria-current={active ? 'step' : undefined} aria-label={`Step ${step.n}: ${step.title}. ${meta?.name || ''}`}>
      <span className="guide-node-num" aria-hidden="true">{step.n}</span>
      <span className="guide-node-ico" aria-hidden="true"><Icon name={meta?.icon || 'documents'} size={20} /></span>
      <span className="guide-node-title">{step.title}</span>
      <span className="guide-node-mod">{meta?.name}</span>
    </Link>
  );
}

function FlowDiagram({ activeIndex }) {
  return (
    <div className="guide-flow" role="group" aria-label="End-to-end process flow">
      {FLOW_LANES.map((lane) => {
        const steps = STEPS.filter((s) => s.lane === lane.id);
        return (
          <section className={`guide-lane guide-lane-${lane.id}`} key={lane.id} aria-label={lane.title}>
            <header className="guide-lane-head"><h3>{lane.title}</h3><p>{lane.note}</p></header>
            <ol className="guide-steps">
              {steps.map((s, i) => (
                <li className="guide-step-item" key={s.id}>
                  <Node step={s} active={activeIndex === s.n - 1} />
                  {(i < steps.length - 1 || lane.end) && <Arrow />}
                </li>
              ))}
              {lane.end && (
                <li className="guide-step-item guide-lane-end">
                  <ScreenLink to={lane.end.to} className="guide-chip">{lane.end.text}</ScreenLink>
                </li>
              )}
            </ol>
          </section>
        );
      })}
    </div>
  );
}

// "Play the process": steps through the diagram with a caption. Play advances by itself; with reduced motion it is manual only.
function Tour({ index, setIndex }) {
  const reduced = useReducedMotion();
  const [playing, setPlaying] = useState(false);
  const last = STEPS.length - 1;
  const step = index >= 0 ? STEPS[index] : null;
  const canOpen = useCanOpen();

  useEffect(() => { if (reduced) setPlaying(false); }, [reduced]);

  useEffect(() => {
    if (!playing) return undefined;
    if (index >= last) { setPlaying(false); return undefined; }
    const t = setTimeout(() => setIndex((i) => Math.min(i + 1, last)), STEP_MS);
    return () => clearTimeout(t);
  }, [playing, index, last, setIndex]);

  // keep the highlighted step in view
  useEffect(() => {
    if (index < 0) return;
    const el = document.querySelector(`.guide-node[data-step="${index + 1}"]`);
    if (el && el.scrollIntoView) el.scrollIntoView({ block: 'center', inline: 'nearest', behavior: reduced ? 'auto' : 'smooth' });
  }, [index, reduced]);

  const next = () => { setPlaying(false); setIndex((i) => Math.min(i + 1, last)); };
  const back = () => { setPlaying(false); setIndex((i) => Math.max(i - 1, 0)); };
  const reset = () => { setPlaying(false); setIndex(-1); };
  const togglePlay = () => {
    if (playing) { setPlaying(false); return; }
    if (index < 0 || index >= last) setIndex(0);
    setPlaying(true);
  };
  const onKeyDown = (e) => {
    if (e.key === 'ArrowRight') { e.preventDefault(); next(); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); back(); }
    else if (e.key === 'Escape') { setPlaying(false); }
  };

  return (
    <section className="guide-tour" aria-label="Play the process" data-playing={playing ? 'true' : 'false'}>
      <div className="guide-tour-controls" role="group" aria-label="Process tour controls" onKeyDown={onKeyDown}>
        {!reduced && (
          <button type="button" className="btn btn-primary" onClick={togglePlay} aria-pressed={playing}>
            <Icon name={playing ? 'pause' : 'play'} size={16} /> {playing ? 'Pause' : 'Play the process'}
          </button>
        )}
        <button type="button" className="btn" onClick={back} disabled={index <= 0} aria-label="Previous step">Back</button>
        <button type="button" className="btn" onClick={next} disabled={index >= last} aria-label="Next step">{index < 0 ? 'Start stepping' : 'Next'}</button>
        <button type="button" className="btn" onClick={reset} disabled={index < 0 && !playing}>Reset</button>
        <span className="guide-tour-count" aria-hidden="true">{index < 0 ? `${STEPS.length} steps` : `Step ${index + 1} of ${STEPS.length}`}</span>
      </div>
      {reduced && <p className="hint guide-tour-note">Automatic playing is switched off because your device asks for reduced motion. Use Next and Back to step through.</p>}
      {playing && !reduced && <div className="guide-progress" aria-hidden="true"><span key={index} /></div>}
      <div className="guide-caption" aria-live="polite" aria-atomic="true">
        {step ? (
          <>
            <h3><span className="guide-caption-num">{step.n}</span> {step.title}</h3>
            <dl>
              <div><dt>What happens</dt><dd>{step.what}</dd></div>
              <div><dt>Data that moves</dt><dd>{step.data}</dd></div>
              <div>
                <dt>Screen to use</dt>
                <dd>
                  <ScreenLink to={step.to} className="guide-caption-link">{step.screen}</ScreenLink>
                  {' · '}<Link to={`/guide/${step.module}`}>How {MODULE_META[step.module]?.name} works</Link>
                  {(step.extras || []).filter((e) => canOpen(e.to)).map((e) => <span key={e.to}>{' · '}<Link to={e.to}>{e.label}</Link></span>)}
                </dd>
              </div>
            </dl>
          </>
        ) : (
          <p>Follow one sale from the first enquiry to the reports, then the buying, project, people, partner, compliance and workplace lanes. Press <strong>Play the process</strong> for a timed tour, or step with Next. You can also click any box below to open that screen.</p>
        )}
      </div>
      <p className="hint guide-tour-keys">Keyboard: Tab to the buttons; while a button has focus, the Left and Right arrow keys step back and forward and Escape pauses.</p>
    </section>
  );
}

export default function Overview() {
  const [index, setIndex] = useState(-1);
  const setIndexSafe = useCallback((fn) => setIndex(fn), []);
  const grid = useMemo(() => MODULE_GUIDES, []);
  const top = useRef(null);
  useEffect(() => { document.title = 'Guide'; }, []);
  return (
    <div className="guide-wide">
      <div className="guide-body" ref={top}>
        <PageHeader title="Guide" description="What the system is, how a sale and a purchase flow through it, and how each module works."
          actions={(<><Link className="btn" to="/guide/integration">How modules connect</Link><Link className="btn" to="/guide/roles">Roles and access</Link><Link className="btn" to="/guide/glossary">Glossary</Link></>)} />
        <section className="card guide-intro" aria-labelledby="guide-what">
          <h2 id="guide-what">What this system is</h2>
          {OVERVIEW.intro.map((p) => <p key={p}>{p}</p>)}
          <ul className="guide-bullets">{OVERVIEW.howToRead.map((t) => <li key={t}>{t}</li>)}</ul>
          {OVERVIEW.notes.map((t) => <p className="hint" key={t}>{t}</p>)}
        </section>

        <section className="card" aria-labelledby="guide-process">
          <div className="card-head"><h2 id="guide-process">The process from end to end</h2></div>
          <p className="muted">Read each lane left to right. Every box is a real screen: click it to open it. The numbers are the order of the tour.</p>
          <Tour index={index} setIndex={setIndexSafe} />
          <FlowDiagram activeIndex={index} />
        </section>

        <section className="card" aria-labelledby="guide-modules">
          <div className="card-head"><h2 id="guide-modules">Module guides</h2></div>
          <ul className="guide-tiles">
            {grid.map((g) => (
              <li key={g.key}>
                <Link to={`/guide/${g.key}`} className="guide-tile" data-module={g.key}>
                  <span className="guide-tile-ico" aria-hidden="true"><Icon name={g.icon} size={22} /></span>
                  <span><strong>{g.name}</strong><span className="guide-tile-purpose">{g.purpose}</span></span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </div>
  );
}
