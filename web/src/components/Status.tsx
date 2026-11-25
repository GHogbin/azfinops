import type { Timing } from "../api";
import { count } from "../format";

export function ErrorBox({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="banner error" role="alert">
      <strong>Could not load data.</strong> {message}
      <button type="button" className="link-btn" onClick={onRetry}>Retry</button>
    </div>
  );
}

/** Shows the server-reported time so slow views are visible, not guessed at. */
export function TimingNote({ timing, loading, bytesHint }: { timing: Timing | null; loading: boolean; bytesHint?: number }) {
  if (!timing || timing.totalMs === null) return null;
  return (
    <p className="muted small timing" aria-live="off">
      {loading ? "Updating… last response " : "Server time "}
      {Math.round(timing.totalMs)} ms{timing.dbMs !== null && ` (database ${Math.round(timing.dbMs)} ms)`}
      {bytesHint ? ` · aggregated from ${count(bytesHint)} source rows` : ""}
    </p>
  );
}
