import type { Filters } from "../api";
import { ResourceChanges } from "./ResourceChanges";

interface Props { filters: Filters }

export function Optimization({ filters }: Props) {
  const ok = !(filters.start && filters.end && filters.start > filters.end);
  if (!ok) return <p className="empty">Fix the date range to load data.</p>;
  return (
    <div>
      <p className="muted small">
        What changed in the estate, in {filters.currency}: resources rightsized or decommissioned since the month before. Unused commitment,
        on-demand spend next to commitments and savings-plan headroom are on the Commitments tab.
      </p>
      <ResourceChanges filters={filters} />
    </div>
  );
}
