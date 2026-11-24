import { num, plain } from '../../format';
import { Badge } from '../../ui';

// The server's messages in plain words (no field names), for the bank pages whose errors are about the whole step.
export function Problem({ error }) {
  if (!error) return null;
  const msgs = Object.values(error.fieldErrors || {}).filter(Boolean);
  return (
    <div className="notice notice-error" role="alert">
      {msgs.length ? (msgs.length === 1 ? msgs[0] : <ul>{msgs.map((m) => <li key={m}>{m}</li>)}</ul>) : error.message}
    </div>
  );
}

export const CONFIDENCE = { high: ['good', 'Sure match'], medium: ['warn', 'Likely match'], low: ['neutral', 'Possible match'] };

export function ConfidenceBadge({ level }) {
  const [tone, label] = CONFIDENCE[level] || CONFIDENCE.low;
  return <Badge tone={tone}>{label}</Badge>;
}

// Money in / out as two cells, the way a bank statement shows it.
export function InOut({ amount, labelIn = 'Money in', labelOut = 'Money out' }) {
  const n = num(amount);
  return (
    <>
      <td className="amount" data-label={labelIn}>{n > 0 ? plain(n) : ''}</td>
      <td className="amount" data-label={labelOut}>{n < 0 ? plain(-n) : ''}</td>
    </>
  );
}

export const signedText = (amount) => {
  const n = num(amount);
  return n >= 0 ? `+${plain(n)}` : `−${plain(-n)}`;
};
