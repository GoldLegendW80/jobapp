/**
 * What the browser holds of one run while it follows it: the stored steps, each once and in
 * order, and the text of the answer being written (issue #33).
 *
 * The run stream sends two things (api/app/agents/live.py):
 * - `step` events, the stored log: each carries its seq, which is also the SSE id, so a
 *   reconnect resumes after the last one;
 * - `partial` events, the text of the content block being written, without an id:
 *   `{block, after, offset, text}`. `offset` counts UTF-16 code units from the start of the
 *   block (what `String.prototype.slice` counts), and a block's text only ever grows.
 *
 * Applying a partial is `text.slice(0, offset) + chunk`, which gives the same result however
 * often and from wherever the server sends it: a new connection starts the block again at
 * offset 0 with everything written so far, and nothing is shown twice or skipped. The live text
 * is therefore always a beginning of the block's text. It is replaced by the block's stored
 * `text` step (the first one after the step numbered `after`), and dropped when the run ends
 * without one (cancelled or failed in the middle of an answer: nothing was stored).
 */
import { FINAL_STATUSES, type RunStatus, type RunStep } from "./runs-api";

export interface TextPart {
  /** Counts the text blocks of the run: a greater one starts a new live text. */
  block: number;
  /** The seq of the last step stored before the block began. */
  after: number;
  /** Where `text` starts in the block, in UTF-16 code units. */
  offset: number;
  text: string;
}

export interface LiveText {
  block: number;
  after: number;
  text: string;
}

export interface StreamState {
  steps: RunStep[];
  live: LiveText | null;
}

export const emptyStream: StreamState = { steps: [], live: null };

function ended(steps: RunStep[]): boolean {
  return steps.some(
    (step) =>
      step.kind === "status" &&
      FINAL_STATUSES.has(step.data.status as RunStatus),
  );
}

/** A stored step arrived. A repeat (a follow restarted from the beginning) changes nothing. */
export function applyStep(state: StreamState, step: RunStep): StreamState {
  if (step.seq <= (state.steps.at(-1)?.seq ?? 0)) return state;
  const steps = [...state.steps, step];
  let live = state.live;
  if (live !== null) {
    const complete = step.kind === "text" && step.seq > live.after;
    const over =
      step.kind === "status" &&
      FINAL_STATUSES.has(step.data.status as RunStatus);
    if (complete || over) live = null;
  }
  return { steps, live };
}

/** A piece of the text being written arrived. */
export function applyPartial(state: StreamState, part: TextPart): StreamState {
  const { steps, live } = state;
  // The block's complete text is already here (a delta that arrives after it), or the run is
  // over: nothing is live.
  if (steps.some((step) => step.kind === "text" && step.seq > part.after))
    return state;
  if (ended(steps)) return state;
  if (live !== null && part.block < live.block) return state;
  const base = live !== null && live.block === part.block ? live.text : "";
  // A gap cannot be filled in: wait for a piece that connects (a new connection starts at 0).
  if (part.offset > base.length) return state;
  const text = base.slice(0, part.offset) + part.text;
  // Never backwards: a piece that only repeats what is shown keeps what is shown.
  if (live !== null && live.block === part.block && base.startsWith(text))
    return state;
  return { steps, live: { block: part.block, after: part.after, text } };
}

/** The newest stored text of the run, if any. */
export function lastText(steps: RunStep[]): string | undefined {
  const found = steps
    .filter(
      (step) => step.kind === "text" && typeof step.data.text === "string",
    )
    .at(-1);
  return found?.data.text as string | undefined;
}
