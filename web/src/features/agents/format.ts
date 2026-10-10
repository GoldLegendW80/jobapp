/** Display helpers of the agent runs: status labels, money, tokens and durations. */
import type { RunStatus } from "./runs-api";

export const STATUS_LABEL: Record<RunStatus, string> = {
  queued: "Queued",
  running: "Running",
  done: "Done",
  failed: "Failed",
  cancelled: "Cancelled",
};

const USD = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 4,
});
const COUNT = new Intl.NumberFormat("en-US");

export function formatCost(value: number | null | undefined): string {
  return value === null || value === undefined ? "None yet" : USD.format(value);
}

export function formatTokens(
  input: number | null | undefined,
  output: number | null | undefined,
): string {
  if (input === null || input === undefined) return "None yet";
  return `${COUNT.format(input)} in, ${COUNT.format(output ?? 0)} out`;
}

/** 4.2 s, 1 min 05 s, 1 h 02 min. */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) {
    return "None yet";
  }
  const total = Math.max(0, seconds);
  if (total < 60) return `${total.toFixed(total < 10 ? 1 : 0)} s`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) {
    return `${minutes} min ${String(Math.floor(total % 60)).padStart(2, "0")} s`;
  }
  return `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, "0")} min`;
}

/** The seconds a run has taken: finished minus started, or until `now` while it runs. */
export function elapsed(
  run: {
    started_at: string | null;
    finished_at: string | null;
    duration_s: number | null;
  },
  now: number,
): number | null {
  if (run.duration_s !== null) return run.duration_s;
  if (!run.started_at) return null;
  const start = Date.parse(run.started_at);
  const end = run.finished_at ? Date.parse(run.finished_at) : now;
  return Number.isNaN(start) ? null : (end - start) / 1000;
}
