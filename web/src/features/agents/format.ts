/**
 * Display helpers of the agent runs: status labels, money, tokens and durations.
 *
 * Money (issue #33): the SDK reports an amount for every run, at API prices. With an Anthropic
 * API key that amount is what the run cost. With a Claude subscription the amount is an
 * estimate and is labelled as one: the run counts toward the plan's usage limits, and what the
 * user pays is set by the plan, which this app cannot see (a plan may charge for extra usage).
 * So no wording says "not billed": the amount is "not a bill" (issue #80). A run whose credential kind
 * was not recorded (older runs, runs that failed before reading their credential) gets a
 * neutral wording: the amount at API prices, without saying it was or was not billed.
 */
import type { CredentialKind, RunStatus } from "./runs-api";

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

type Kind = CredentialKind | null | undefined;

/** The credential kind a `usage` step carries (issue #80), or undefined when it carries none
 * (steps stored before, or a value this page does not know). */
export function kindOfUsage(data: Record<string, unknown>): Kind {
  const kind = data.credential_kind;
  return kind === "anthropic_api_key" || kind === "claude_subscription"
    ? kind
    : undefined;
}

/** The heading over the amount: "Cost", or what it is when it is not one. */
export function costLabel(kind: Kind): string {
  if (kind === "anthropic_api_key") return "Cost";
  if (kind === "claude_subscription") return "Estimate at API prices";
  return "Amount at API prices";
}

/** A short qualifier to put next to a bare amount, or null when it is a plain cost. */
export function costQualifier(kind: Kind): string | null {
  if (kind === "anthropic_api_key") return null;
  if (kind === "claude_subscription") return "Estimate, not a bill";
  return "At API prices";
}

/** One sentence saying what the amount is, or null when it is a plain cost. */
export function costNote(kind: Kind): string | null {
  if (kind === "anthropic_api_key") return null;
  if (kind === "claude_subscription") {
    return "This run used your Claude subscription and counts toward your plan's usage limits. What you pay is set by your plan, not by this amount: the estimate is what the same tokens cost at API prices.";
  }
  return "The kind of credential this run used was not recorded, so whether this amount was billed is not known.";
}

/** The amount inside a sentence: "$0.19", "$0.19 estimated at API prices, not a bill". */
export function describeCost(
  value: number | null | undefined,
  kind: Kind,
): string {
  const amount = formatCost(value);
  if (value === null || value === undefined) return amount;
  if (kind === "anthropic_api_key") return amount;
  if (kind === "claude_subscription") {
    return `${amount} estimated at API prices, not a bill`;
  }
  return `${amount} at API prices`;
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
