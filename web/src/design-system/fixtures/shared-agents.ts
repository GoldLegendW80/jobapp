import { delay, http, HttpResponse, sse } from "msw";

import type { ChatMessage, Thread } from "@/features/agents/api/chat-api";
import type { Proposal } from "@/features/agents/api/proposals-api";
import type { Run, RunStep } from "@/features/agents/api/runs-api";
import type { TextPart } from "@/features/agents/helpers/live-text";
import { server } from "@/test/server";

import { SAVED_CREDENTIAL } from "./account";
import { POSTING_DETAIL } from "./postings";
import { FINISHED_STEPS, makeRun, RUNNING_STEPS } from "./runs";

/** Invented runs, conversations and proposals for the stories of the shared agent components. */

const step = (
  seq: number,
  kind: string,
  data: Record<string, unknown> = {},
): RunStep => ({ seq, at: "2026-11-05T08:14:05Z", kind, data });

/** The answer of the posting check, as the stream writes it before it is stored. */
export const ANSWER_SO_FAR =
  "The posting fits well: SQL, dbt and Looker are all in your profile. Healthcare data";

/** A run at work whose answer is being written: the steps so far, and the text after them. */
export const WRITING: { steps: RunStep[]; part: TextPart } = {
  steps: RUNNING_STEPS,
  part: { block: 1, after: 6, offset: 0, text: ANSWER_SO_FAR },
};

/** A run that stopped on its turn limit, after a refused tool, a rate limit and a model error. */
export const FAILED_STEPS: RunStep[] = [
  ...FINISHED_STEPS.slice(0, 3),
  step(4, "tool_denied", { name: "mcp__app__send_mail" }),
  step(5, "rate_limit", {
    status: "Waiting 20 seconds before the next request.",
  }),
  step(6, "api_error", { error: "The model answered 529: overloaded." }),
  step(7, "tool_call", { name: "mcp__app__get_profile", input: {} }),
  step(8, "tool_result", {
    is_error: true,
    content: "The profile could not be read.",
  }),
  step(9, "status", { status: "failed", error: "max_turns" }),
];

/** A private run stores sizes, never the content. */
export const PRIVATE_STEPS: RunStep[] = [
  ...FINISHED_STEPS.slice(0, 3),
  step(4, "tool_call", { name: "mcp__app__get_profile", input_size: 48 }),
  step(5, "tool_result", { size: 18_204, blocks: 2 }),
  step(6, "status", { status: "done" }),
];

/** A tool result longer than the timeline shows unfolded. */
export const LONG_RESULT_STEPS: RunStep[] = [
  ...FINISHED_STEPS.slice(0, 5),
  step(6, "tool_result", {
    content: JSON.stringify(
      Array.from({ length: 40 }, (_, index) => ({
        company: `Invented company ${String(index + 1)}`,
        title: "Data engineer, streaming platform and warehouse",
        location: "Remote, France",
      })),
      null,
      2,
    ),
  }),
  step(7, "text", {
    text: "Forty postings match the profile. The strongest three ask for streaming experience you already have: Kafka in production, dbt on a warehouse, and on call duty for the pipelines you shipped. The other ones lean on tools you have not used yet, so each letter should say how fast you picked up the last new one.\n\nI would start with the three in Lyon and Nantes, then the remote ones.",
  }),
  step(8, "status", { status: "done" }),
];

/** One run a story streams: its detail, its steps, then the end unless it is still at work. */
export interface FakeRun {
  run: Run;
  steps?: RunStep[];
  /** The text being written, sent after the steps; the stream stays open. */
  writing?: TextPart;
  /** The stream stays open after the steps: the run is still at work. */
  open?: boolean;
  /** The run was deleted: its detail answers 404 and its stream says so. */
  gone?: boolean;
  /** Steps sent one by one after the first ones, `pause` milliseconds apart. */
  later?: RunStep[];
  pause?: number;
  /** Called once the last step was sent, before the end. */
  onDone?: () => void;
}

/** GET /api/agents/runs/:id and its stream for each run given; any other id answers 404. */
export function fakeRuns(...runs: FakeRun[]) {
  const byId = new Map(runs.map((one) => [one.run.id, one]));
  server.use(
    http.get("*/api/agents/runs/:id", ({ params }) => {
      const one = byId.get(String(params.id));
      if (!one || one.gone)
        return HttpResponse.json(
          { detail: "This run does not exist." },
          { status: 404 },
        );
      return HttpResponse.json({
        ...one.run,
        events: [],
        events_truncated: false,
      });
    }),
    http.post("*/api/agents/runs/:id/cancel", ({ params }) => {
      const one = byId.get(String(params.id));
      return HttpResponse.json(
        makeRun({ ...one?.run, status: "running", cancel_requested: true }),
      );
    }),
    sse<{ step: RunStep; partial: TextPart; end: object; gone: object }>(
      "*/api/agents/runs/:id/stream",
      async ({ client, params }) => {
        const one = byId.get(String(params.id));
        if (!one || one.gone) {
          client.send({ event: "gone", data: {} });
          return;
        }
        for (const each of one.steps ?? FINISHED_STEPS)
          client.send({ event: "step", data: each });
        for (const each of one.later ?? []) {
          await delay(one.pause ?? 700);
          client.send({ event: "step", data: each });
        }
        if (one.writing) {
          client.send({ event: "partial", data: one.writing });
          return;
        }
        one.onDone?.();
        if (!one.open) client.send({ event: "end", data: {} });
      },
    ),
  );
}

/** Every read of one run never answers, or fails, or its stream is down. */
export function failRun(answer: "loading" | "error" | "stream down") {
  if (answer === "stream down") {
    server.use(
      http.get("*/api/agents/runs/:id/stream", () =>
        HttpResponse.json(
          { detail: "The server is restarting." },
          { status: 503 },
        ),
      ),
    );
    return;
  }
  server.use(
    http.get("*/api/agents/runs/:id", async () => {
      if (answer === "loading") await delay("infinite");
      return HttpResponse.json(
        { detail: "Something went wrong on the server." },
        { status: 500 },
      );
    }),
  );
}

/** The person saved a model credential: the agents can run. */
export function fakeCredential() {
  server.use(
    http.get("*/api/me/model-credential", () =>
      HttpResponse.json(SAVED_CREDENTIAL),
    ),
  );
}

/* --- the chat about a posting ---------------------------------------------------------------- */

export const THREAD: Thread = {
  id: "c7d1e2f3-4a5b-4c6d-8e9f-0a1b2c3d4e51",
  job_id: POSTING_DETAIL.id,
  title: POSTING_DETAIL.title,
  created_at: "2026-11-05T09:00:00Z",
  updated_at: "2026-11-05T09:04:00Z",
};

const message = (
  id: number,
  role: ChatMessage["role"],
  text: string,
  changes: Partial<ChatMessage> = {},
): ChatMessage => ({
  id,
  role,
  text,
  run_id: `run-${String(Math.ceil(id / 2))}`,
  run_status: "done",
  error: null,
  at: `2026-11-05T09:0${String(id)}:00Z`,
  ...changes,
});

/** Two questions about the posting and their answers. */
export const CONVERSATION: ChatMessage[] = [
  message(1, "user", "Does this role fit my profile?"),
  message(
    2,
    "assistant",
    "Mostly, yes. The posting asks for four years of Python and SQL, which you have, and a pipeline you still run: your freight tracking pipeline at Atelier Brume is a good example.\n\nLogistics is new to you. The letter can say how you learned the pricing domain in your last role.",
  ),
  message(3, "user", "What should I stress in my application?"),
  message(
    4,
    "assistant",
    "Three things: the pipeline you shipped and still operate, the on call duty you took for it, and your curiosity about logistics, with one concrete example.",
  ),
];

/** The same conversation, then a question whose answer failed. */
export const UNANSWERED: ChatMessage[] = [
  ...CONVERSATION,
  message(5, "user", "Which skills should I add before applying?", {
    run_id: "run-failed",
    run_status: "failed",
    error: "the model was overloaded. Send the question again.",
  }),
];

/** A long conversation: the server sends the newest messages only. */
export const LONG_CONVERSATION: ChatMessage[] = Array.from(
  { length: 6 },
  (_, round) => [
    message(
      round * 2 + 1,
      "user",
      `Question ${String(round + 1)}: what does the posting say about the team?`,
    ),
    message(
      round * 2 + 2,
      "assistant",
      "The team is six engineers in Nantes and Lyon, with one data analyst. The posting says they rotate on call weekly and review every pipeline change in pairs.",
    ),
  ],
).flat();

/** The run that answers a message sent in a story. */
export const REPLY_RUN = makeRun({
  id: "run-reply",
  agent: "chat",
  status: "running",
  finished_at: null,
  duration_s: null,
});

const REPLY =
  "The posting does not name a salary. Postings of this level in Lyon usually say between 55,000 and 65,000 euros a year; ask in the first call.";

/**
 * The job's conversation, in memory: the thread and its messages (`none` before the first one),
 * the run answering now, and a message sent in the story answered live by the chat agent.
 */
export function fakeThread(
  initial: {
    messages?: ChatMessage[];
    truncated?: boolean;
    none?: boolean;
    /** The answer being written now, its text so far. */
    writing?: string;
    read?: "ok" | "loading" | "error";
  } = {},
) {
  const state = {
    exists: initial.none !== true,
    messages: [...(initial.messages ?? CONVERSATION)],
    active: initial.writing ? "run-writing" : (null as string | null),
    next: 100,
  };
  const detail = () => ({
    ...THREAD,
    messages: state.messages,
    messages_truncated: initial.truncated ?? false,
    active_run_id: state.active,
  });
  server.use(
    http.get("*/api/agents/threads", async () => {
      if (initial.read === "loading") await delay("infinite");
      if (initial.read === "error")
        return HttpResponse.json(
          { detail: "Something went wrong on the server." },
          { status: 500 },
        );
      return HttpResponse.json({ items: state.exists ? [THREAD] : [] });
    }),
    http.post("*/api/agents/threads", () => {
      state.exists = true;
      return HttpResponse.json(THREAD, { status: 201 });
    }),
    http.get("*/api/agents/threads/:id", () =>
      state.exists
        ? HttpResponse.json(detail())
        : HttpResponse.json({ detail: "Not found." }, { status: 404 }),
    ),
    http.delete("*/api/agents/threads/:id", () => {
      state.exists = false;
      state.messages = [];
      return new HttpResponse(null, { status: 204 });
    }),
    http.post("*/api/agents/threads/:id/messages", async ({ request }) => {
      const { text } = (await request.json()) as { text: string };
      state.next += 2;
      const sent = message(state.next, "user", text, {
        run_id: REPLY_RUN.id,
        run_status: "running",
      });
      state.messages.push(sent);
      state.active = REPLY_RUN.id;
      return HttpResponse.json({ message: sent, run: REPLY_RUN });
    }),
  );
  fakeRuns(
    {
      run: makeRun({ ...REPLY_RUN, id: "run-writing" }),
      steps: RUNNING_STEPS,
      writing: initial.writing
        ? { block: 1, after: 6, offset: 0, text: initial.writing }
        : undefined,
    },
    {
      run: REPLY_RUN,
      steps: [step(1, "status", { status: "running" })],
      later: [
        step(2, "tool_call", { name: "mcp__app__get_job", input: {} }),
        step(3, "text", { text: REPLY }),
        step(4, "status", { status: "done" }),
      ],
      pause: 900,
      onDone: () => {
        state.messages = state.messages.map((one) =>
          one.run_id === REPLY_RUN.id ? { ...one, run_status: "done" } : one,
        );
        state.next += 1;
        state.messages.push(
          message(state.next, "assistant", REPLY, { run_id: REPLY_RUN.id }),
        );
        state.active = null;
      },
    },
  );
  return state;
}

/* --- the posting check and its proposals ----------------------------------------------------- */

export const TRIAGE_RUN = makeRun({
  id: "run-triage",
  job_id: POSTING_DETAIL.id,
  job: {
    id: POSTING_DETAIL.id,
    company: POSTING_DETAIL.company,
    title: POSTING_DETAIL.title,
  },
});

const proposal = (
  id: string,
  kind: "job_note" | "job_status",
  payload: Record<string, unknown>,
  status: Proposal["status"] = "pending",
): Proposal => ({
  id,
  run_id: TRIAGE_RUN.id,
  target: "job",
  target_id: POSTING_DETAIL.id,
  kind,
  payload,
  status,
  decided_by: status === "pending" ? null : "user",
  decided_at: status === "pending" ? null : "2026-11-05T08:20:00Z",
  edited_value: null,
  reason: null,
  created_at: "2026-11-05T08:14:21Z",
});

/** What the check proposes: a note and a status, both waiting for the person. */
export const PENDING_PROPOSALS: Proposal[] = [
  proposal("proposal-note", "job_note", {
    text: "Strong fit on SQL, dbt and Looker. Healthcare data standards are new: say in the letter how you learned the pricing domain in your last role.",
  }),
  proposal("proposal-status", "job_status", {
    from: "found",
    status: "shortlisted",
    reason: "The skills match and the place fits your search: worth applying.",
  }),
];

/** The proposals of the last check, decided. */
export const DECIDED_PROPOSALS: Proposal[] = [
  proposal(
    "proposal-note",
    "job_note",
    { text: "Strong fit on SQL, dbt and Looker." },
    "accepted",
  ),
  proposal(
    "proposal-status",
    "job_status",
    {
      from: "found",
      status: "shortlisted",
      reason: "The skills match: worth applying.",
    },
    "rejected",
  ),
  proposal(
    "proposal-old",
    "job_status",
    { from: "found", status: "closed", reason: "The posting looked closed." },
    "superseded",
  ),
];

/**
 * The posting check of one job, in memory: its latest triage run, and what it proposed. A
 * click on "Assess with agent" starts a run that works for a few seconds, then proposes.
 */
export function fakeAssessment(
  initial: {
    run?: FakeRun | null;
    proposals?: Proposal[];
    runsRead?: "ok" | "loading";
  } = {},
) {
  const state = {
    run: initial.run?.run ?? null,
    proposals: [...(initial.proposals ?? [])],
  };
  const started: FakeRun = {
    run: makeRun({
      ...TRIAGE_RUN,
      id: "run-started",
      status: "running",
      finished_at: null,
      duration_s: null,
      cost_usd: null,
    }),
    steps: RUNNING_STEPS,
    later: FINISHED_STEPS.slice(6),
    pause: 1200,
    onDone: () => {
      // The detail and the job's latest run read the same object: both say done from now on.
      started.run = makeRun({ ...TRIAGE_RUN, id: "run-started" });
      state.run = started.run;
      state.proposals = PENDING_PROPOSALS.map((one) => ({
        ...one,
        run_id: started.run.id,
      }));
    },
  };
  server.use(
    http.get("*/api/agents/runs", async () => {
      if (initial.runsRead === "loading") await delay("infinite");
      return HttpResponse.json({ items: state.run ? [state.run] : [] });
    }),
    http.get("*/api/proposals", () =>
      HttpResponse.json({ items: state.proposals }),
    ),
    http.post("*/api/agents/runs", () => {
      state.run = started.run;
      return HttpResponse.json(state.run, { status: 201 });
    }),
    http.post("*/api/proposals/:id/:decision", ({ params }) => {
      const decided: Proposal["status"] =
        String(params.decision) === "accept" ? "accepted" : "rejected";
      state.proposals = state.proposals.map((one) =>
        one.id === params.id ? { ...one, status: decided } : one,
      );
      return HttpResponse.json(
        state.proposals.find((one) => one.id === params.id),
      );
    }),
  );
  fakeRuns(...(initial.run ? [initial.run] : []), started);
  return state;
}
