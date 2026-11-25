import type { ReactNode } from "react";

interface Props {
  label: string;
  /** Omit (or pass "") to show no large figure, when the card's contents already carry it. */
  value?: string;
  /** The exact value, shown on hover when `value` is a short form such as "$1.2M". */
  full?: string;
  note?: string;
  /** Plain-language definition shown on hover/focus. */
  info?: string;
  /** Larger tile for the headline figure. */
  hero?: boolean;
  children?: ReactNode;
}

export function Kpi({ label, value, full, note, info, hero, children }: Props) {
  return (
    <div className={hero ? "kpi hero" : "kpi"}>
      <div className="kpi-label">
        {label}
        {info && <span className="info" tabIndex={0} role="note" title={info} aria-label={`${label}: ${info}`}>i</span>}
      </div>
      {value && <div className="kpi-value" title={full && full !== value ? full : undefined}>{value}</div>}
      {note && <div className="kpi-note">{note}</div>}
      {children}
    </div>
  );
}
