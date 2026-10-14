/**
 * What the Apply section shows for each state of an application (SPEC-apply.md, "States"), as pure
 * functions: the phase that picks the panel, the status line, how often to read again, the
 * sentences for what stopped a run. The api's own sentences (a refusal, a gate answer) are shown
 * as they come; these are only the ones the page writes itself. No dashes as separators.
 */
import type {
  Answer,
  Application,
  ApplicationEvent,
  Approval,
  ApprovalDocument,
} from "./apply-api";

export const APPLY_POLL_MS = 1500;

export type Phase =
  | "idle"
  | "waiting"
  | "filling"
  | "needs"
  | "review"
  | "submitting"
  | "submitted"
  | "unknown"
  | "refused"
  | "other";

const PHASES: Record<string, Phase> = {
  queued: "waiting",
  filling: "filling",
  needs_person: "needs",
  ready: "review",
  approved: "submitting",
  held: "submitting",
  submitting: "submitting",
  submitted: "submitted",
  submit_unknown: "unknown",
  refused_by_site: "refused",
  cancelled: "idle",
  failed: "idle",
  blocked: "idle",
  posting_closed: "idle",
  rehearsed: "idle",
};

/** A state this page does not know is never treated as a state where something can be clicked. */
export function phaseOf(application: Application | null | undefined): Phase {
  if (!application) return "idle";
  return PHASES[application.state] ?? "other";
}

/** The phases in which the app works and the page reads again by itself. */
const BUSY: ReadonlySet<Phase> = new Set(["waiting", "filling", "submitting"]);

export function pollInterval(
  application: Application | null | undefined,
): number | false {
  return BUSY.has(phaseOf(application)) ? APPLY_POLL_MS : false;
}

/** The phases that have a form behind them to read: the fields, the documents, the screenshots. */
const WITH_FORM: ReadonlySet<Phase> = new Set([
  "needs",
  "review",
  "submitting",
  "submitted",
  "unknown",
  "refused",
]);

export const hasForm = (application: Application | null | undefined) =>
  WITH_FORM.has(phaseOf(application));

/** The run report is shown once the application reached the site or ended. */
const WITH_REPORT: ReadonlySet<Phase> = new Set([
  "submitted",
  "unknown",
  "refused",
]);

export const hasReport = (application: Application | null | undefined) =>
  WITH_REPORT.has(phaseOf(application));

const NEEDS: Record<string, string> = {
  captcha: "The site asks for a captcha, so the assistant stopped.",
  signin: "The site asks you to sign in, so the assistant stopped.",
  email_code: "The site sent a code by e-mail that only you can read.",
  left_required: "A required question is left for you to answer.",
  payment_requested: "The site asks for a payment, so the assistant stopped.",
  no_adapter: "The assistant does not know this form yet.",
};

const FAILURES: Record<string, string> = {
  interrupted: "it was interrupted",
  internal_error: "something went wrong on our side",
  form_changed: "the form changed while it was being filled",
};

/** The status line under the section's title. */
export function statusLine(application: Application | null | undefined) {
  if (!application)
    return "No application yet. Nothing is sent without your approval.";
  switch (application.state) {
    case "queued":
      return "Waiting for a browser. The form is filled within a minute.";
    case "filling":
      return "Filling the form. You can leave this page: the fill goes on, and you will find the result here.";
    case "needs_person":
      return `${NEEDS[application.needs ?? ""] ?? "The form needs you before it can go on."} Nothing was sent.`;
    case "ready":
      return "Filled and waiting for your approval. Nothing is sent until you approve.";
    case "approved":
    case "held":
    case "submitting":
      return "Approved. The form is filled again with exactly the answers you approved, then submitted.";
    case "submitted":
      return "Applied. The form was submitted.";
    case "submit_unknown":
      return "We could not confirm this application. Look for a confirmation mail or on the site.";
    case "refused_by_site":
      return "The site showed errors after the click, so it did not accept the application. Check the screenshot, then try again.";
    case "cancelled":
      return "The last application was cancelled. Nothing was sent.";
    case "failed": {
      const why = FAILURES[application.error_code ?? ""];
      return `The last attempt failed${why ? `: ${why}` : ""}. Nothing was sent.`;
    }
    case "blocked":
      return "The last attempt was blocked by a rule. Nothing was sent.";
    case "posting_closed":
      return "The site says this posting is closed. Nothing was sent.";
    case "rehearsed":
      return "A rehearsal ran. Nothing was sent.";
    default:
      return "This application is in a state this page does not know. Nothing more can be done here.";
  }
}

// --- the fields ---------------------------------------------------------------------------------

const SOURCES: Record<string, string> = {
  identity: "Your details",
  document: "Your documents",
  answers: "Your answers",
  bank: "Saved answer",
  agent: "Assistant",
  person: "You",
  left: "You answer",
};

export const sourceLabel = (source: string) => SOURCES[source] ?? source;

/** A value as the person reads it. */
export function valueText(value: unknown): string {
  if (value === null || value === undefined || value === "") return "";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value))
    return value.map(valueText).filter(Boolean).join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  if (typeof value === "string") return value;
  return typeof value === "number" ? String(value) : "";
}

/** Left for the person and still empty. A value the person typed ends the leaving. */
export const isOpenField = (answer: Answer) =>
  answer.you_answer && valueText(answer.value) === "";

export const leftFields = (answers: readonly Answer[]) =>
  answers.filter(isOpenField);

/** Left, required and still empty: the form cannot go without it, so Approve waits. */
export const blockingFields = (answers: readonly Answer[]) =>
  leftFields(answers).filter((answer) => answer.required);

const YES_NO_KINDS = new Set(["yes_no", "boolean", "checkbox"]);

/** The control an answer is typed with. */
export function editorKind(
  answer: Answer,
): "yes_no" | "select" | "long" | "text" {
  if (YES_NO_KINDS.has(answer.kind) || typeof answer.value === "boolean") {
    return "yes_no";
  }
  if ((answer.options ?? []).length > 0) return "select";
  if (answer.kind === "textarea" || valueText(answer.value).length > 80) {
    return "long";
  }
  return "text";
}

// --- the documents ------------------------------------------------------------------------------

const DOCUMENT_NAMES: Record<string, string> = { cv: "CV", letter: "letter" };

export function documentLine(kind: string, one: ApprovalDocument): string {
  const name = DOCUMENT_NAMES[kind] ?? kind;
  const version =
    typeof one.number === "number" ? ` version ${one.number}` : "";
  return `${name.charAt(0).toUpperCase()}${name.slice(1)}${version}`;
}

export function documentsSentence(approval: Approval): string {
  const lines = Object.entries(approval.documents).map(([kind, one]) =>
    documentLine(kind, one),
  );
  return lines.length > 0 ? lines.join(" and ") : "No document";
}

// --- the run report -----------------------------------------------------------------------------

const EVENTS: Record<string, string> = {
  created: "Application started",
  waiting: "Waiting for the documents",
  lease: "Browser ready",
  filled: "Form filled",
  decision: "Your approval checked",
  click: "Submit clicked",
  cancelled: "Cancelled",
  rehearsed: "Rehearsal done",
  refused: "Refused",
  retry: "Back to your approval",
  person_confirmed: "You confirmed it was submitted",
  kill_switch: "Submissions switched off, nothing sent",
  submit_stopped: "Submit stopped before the click",
  screenshot_failed: "A screenshot could not be taken",
  ended: "Run ended",
};

export function eventLine(event: ApplicationEvent): string {
  if (event.kind === "screenshot") {
    return event.data.kind === "after"
      ? "Screenshot after the click"
      : "Screenshot before the click";
  }
  const known = EVENTS[event.kind];
  if (known) return known;
  const words = event.kind.replace(/_/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** From the start to the submission, or to the last change, as "1 min 12 s". */
export function durationText(application: Application): string | null {
  const start = Date.parse(application.created_at);
  const end = Date.parse(application.submitted_at ?? application.updated_at);
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return null;
  const seconds = Math.round((end - start) / 1000);
  if (seconds < 60) return `${seconds} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ${seconds % 60} s`;
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

/** The newest screenshot of a kind (the api lists them oldest first). */
export function lastShot(approval: Approval | undefined, kind: string) {
  const shots = (approval?.screenshots ?? []).filter((s) => s.kind === kind);
  return shots[shots.length - 1];
}
