import type { TopRow } from "../api";
import { count, money, moneyShort, num, pct } from "../format";
import { acctName, enrollmentColor } from "./EnrollmentSplit";
import { rememberLabel } from "../labels";

interface Props {
  title: string;
  rows: TopRow[];
  other: string;
  otherSplit?: { account: string; cost: string }[];
  total: string;
  groups: number;
  currency: string;
  /** Clicking a name narrows the whole workspace to that value. */
  onPick?: (value: string) => void;
  /** Opens the value in the Cost Explorer, one level deeper. */
  onOpen?: (value: string) => void;
}

const GUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
/** Values the export uses for "nothing here": shown in italics so they read as a gap to fix, not a name. */
const PLACEHOLDER = /^\((none|untagged|unattributed|unknown|blank)\)$/i;

/** A ranked list: one line per value with its cost and share. Rows plus "all others" always add up to the filter total. */
export function BarList({ title, rows, other, otherSplit, total, groups, currency, onPick, onOpen }: Props) {
  type Sp = { account: string; cost: string }[] | undefined;
  const splitText = (sp: Sp) => (sp && sp.length > 1 ? sp.map((x) => `${acctName(x.account)} ${moneyShort(x.cost, currency)}`).join(", ") : "");
  /** One dot per enrollment that has cost in the row, so a shared value is visible without a bar. */
  const dots = (sp: Sp) => {
    const shown = (sp ?? []).filter((x) => num(x.cost) > 0);
    if (shown.length < 2) return <span />;                    // keeps the grid columns lined up
    return (
      <span className="rank-dots" title={splitText(sp)} aria-hidden="true">
        {shown.map((x) => <span key={x.account} className="enr-dot" style={{ background: enrollmentColor(x.account) }} />)}
      </span>
    );
  };
  const tot = num(total);
  rows.forEach((r) => rememberLabel(r.key, r.label));
  const share = (c: string): string => (tot === 0 ? "n/a" : pct(num(c) / tot));
  const hidden = Math.max(0, groups - rows.length);
  const shownCost = rows.reduce((a, r) => a + num(r.cost), 0);
  return (
    <section className="card" aria-label={title}>
      <header className="card-head">
        <h3>{title}</h3>
        <span className="muted small">{hidden > 0 && tot !== 0 ? `top ${rows.length} = ${pct(shownCost / tot)} · ` : ""}{count(groups)} groups</span>
      </header>
      {rows.length === 0 ? <p className="empty">No cost in this selection.</p> : (
        <ol className="ranked">
          {rows.map((r, i) => {
            const name = r.label ?? r.key ?? "(none)";
            const placeholder = r.key === null || PLACEHOLDER.test(name);
            const id = r.label && r.key && r.key !== r.label ? r.key : null;
            return (
              <li key={r.key ?? "(null)"}>
                <span className="rank-n muted">{i + 1}</span>
                <span className="rank-name" title={id ? `${name}\n${GUID.test(id) ? "ID " : ""}${id}` : name}>
                  {onPick && r.key !== null
                    ? <button type="button" className={placeholder ? "link-btn placeholder" : "link-btn"} onClick={() => onPick(r.key as string)}>{name}</button>
                    : <span className={placeholder ? "placeholder" : undefined}>{name}</span>}
                </span>
                {dots(r.split)}
                {onOpen && r.key !== null
                  ? <button type="button" className="open-btn" onClick={() => onOpen(r.key as string)} aria-label={`Open ${name} in Explorer`} title="Open in Explorer">↗</button>
                  : <span />}
                <span className="rank-value">{money(r.cost, currency)}</span>
                <span className="rank-share muted small">{share(r.cost)}</span>
                {splitText(r.split) && <span className="sr-only">By enrollment: {splitText(r.split)}</span>}
              </li>
            );
          })}
          {hidden > 0 && (
            <li className="rank-other">
              <span />
              <span className="rank-name muted">All others ({count(hidden)})</span>
              {dots(otherSplit)}
              <span />
              <span className="rank-value">{money(other, currency)}</span>
              <span className="rank-share muted small">{share(other)}</span>
              {splitText(otherSplit) && <span className="sr-only">By enrollment: {splitText(otherSplit)}</span>}
            </li>
          )}
        </ol>
      )}
    </section>
  );
}
