import { http, HttpResponse } from "msw";

import type { Answer } from "@/features/apply/apply-api";
import type { QueueItem, Summary } from "@/features/apply/queue-api";

import { fakeApplication } from "./apply";
import { server } from "./server";

const WAITING = ["ready", "needs_person", "refused_by_site", "submit_unknown"];

/** An item of the review queue: application `n` of posting `n`, both documents on one page. */
export function queueItem(
  n: number,
  state: string,
  overrides: Partial<QueueItem> = {},
): QueueItem {
  const tail = String(n).padStart(12, "0");
  return {
    ...fakeApplication(state, {
      id: `00000000-0000-4000-a000-${tail}`,
      posting_id: `00000000-0000-4000-9000-${tail}`,
    }),
    company: "Example Robotics",
    title: `Robotics Engineer ${n}`,
    shot_id: null,
    left: 0,
    required_left: 0,
    documents: {
      cv: { number: 3, fit: "one_page", corrections: 0 },
      letter: { number: 2, fit: "one_page", corrections: 0 },
    },
    documents_review: false,
    reason: null,
    ...overrides,
  };
}

type Refusal = { detail: string; code: string };

/**
 * The queue routes and A6's approval routes as the api answers them, in memory: Reject and Cancel
 * take the item out of the queue, Approve moves it under way, an answer saved changes the hash.
 * Every write is recorded; `next` makes the next decision answer 409 with that refusal.
 */
export function fakeQueue(
  items: QueueItem[],
  answers: Record<string, Answer[]> = {},
) {
  const state = {
    items,
    answers,
    versions: {} as Record<string, number>,
    reads: 0,
    puts: [] as { id: string; field: string; value: unknown }[],
    approves: [] as { id: string; hash: string }[],
    rejects: [] as { id: string; skip_posting: boolean }[],
    cancels: [] as string[],
    next: null as Refusal | null,
    /** The next approve answers 409 stale with these answers. */
    stale: null as Answer[] | null,
    /** The server's time the queue answers with (the countdown reads it, not the page's clock). */
    now: undefined as string | undefined,
    mode: "review",
    summary: {
      since: "2026-10-14T00:00:00Z",
      submitted: 0,
      held: 0,
      sent_to_review: [],
    } as Summary,
    rehearsals: [] as { id: string; answer: string }[],
    hash: (id: string) => `hash-${id}-${state.versions[id] ?? 1}`,
    waiting: () =>
      state.items.filter(
        (item) =>
          (WAITING.includes(item.state) &&
            !(item.state === "ready" && item.mode === "full_auto")) ||
          item.rehearsal,
      ).length,
    view: (id: string) => ({
      state: state.items.find((item) => item.id === id)?.state ?? "cancelled",
      dry_run: false,
      answers_hash: state.hash(id),
      answers: state.answers[id] ?? [],
      documents: {
        cv: { version_id: "v-cv", number: 3, fit: "one_page", corrections: 0 },
        letter: {
          version_id: "v-letter",
          number: 2,
          fit: "one_page",
          corrections: 0,
        },
      },
      screenshots: [],
    }),
  };
  const refused = () => {
    const refusal = state.next;
    state.next = null;
    return refusal
      ? HttpResponse.json({ ...refusal, application_id: null }, { status: 409 })
      : null;
  };
  const take = (id: string) => {
    const item = state.items.find((one) => one.id === id)!;
    state.items = state.items.filter((one) => one.id !== id);
    return item;
  };
  server.use(
    http.get("*/api/apply/queue", () =>
      HttpResponse.json({
        items: state.items,
        waiting_on_you: state.waiting(),
        ...(state.now ? { now: state.now } : {}),
      }),
    ),
    http.get("*/api/apply/summary", () => HttpResponse.json(state.summary)),
    http.get("*/api/apply/settings", () =>
      HttpResponse.json({
        mode_after_like: state.mode,
        full_auto_available: true,
        paused: false,
        min_fit: 80,
        min_fit_chosen: true,
        safety_ceiling: 30,
        hold_s: 900,
        hold_min_s: 900,
        company_days: 30,
        show_postings_from: null,
        write_documents_from: null,
        rehearsals: 3,
        rehearsals_done: 3,
        full_auto_since: null,
        review_before_send: false,
      }),
    ),
    http.post(
      "*/api/applications/:id/rehearsal",
      async ({ params, request }) => {
        const id = String(params.id);
        const { answer } = (await request.json()) as { answer: string };
        state.rehearsals.push({ id, answer });
        const refusal = refused();
        if (refusal) return refusal;
        if (answer === "no") {
          take(id);
          return HttpResponse.json(fakeApplication("rehearsed", { id }));
        }
        state.items = state.items.map((item) =>
          item.id === id
            ? { ...item, state: "ready", mode: "review", rehearsal: false }
            : item,
        );
        return HttpResponse.json(
          fakeApplication("ready", { id, posting_id: id }),
        );
      },
    ),
    http.get("*/api/apply/queue/count", () =>
      HttpResponse.json({ waiting_on_you: state.waiting() }),
    ),
    http.post("*/api/apply/queue/:id/reject", async ({ params, request }) => {
      const id = String(params.id);
      const body = (await request.json()) as { skip_posting: boolean };
      state.rejects.push({ id, skip_posting: body.skip_posting });
      const refusal = refused();
      if (refusal) return refusal;
      const item = take(id);
      return HttpResponse.json({
        application: { ...item, state: "cancelled" },
        posting_status: body.skip_posting ? "skipped" : "found",
      });
    }),
    http.get("*/api/applications/:id/approval", ({ params }) => {
      state.reads += 1;
      return HttpResponse.json(state.view(String(params.id)));
    }),
    http.put(
      "*/api/applications/:id/answers/:field",
      async ({ params, request }) => {
        const id = String(params.id);
        const field = String(params.field);
        const { value } = (await request.json()) as { value: unknown };
        state.puts.push({ id, field, value });
        state.answers[id] = (state.answers[id] ?? []).map((one) =>
          one.field_key === field
            ? { ...one, value, source: "person", you_answer: false }
            : one,
        );
        state.versions[id] = (state.versions[id] ?? 1) + 1;
        state.items = state.items.map((item) =>
          item.id === id
            ? {
                ...item,
                left: Math.max(0, item.left - 1),
                required_left: Math.max(0, item.required_left - 1),
              }
            : item,
        );
        return HttpResponse.json(state.view(id));
      },
    ),
    http.post("*/api/applications/:id/approve", async ({ params, request }) => {
      const id = String(params.id);
      const { answers_hash } = (await request.json()) as {
        answers_hash: string;
      };
      state.approves.push({ id, hash: answers_hash });
      if (state.stale) {
        state.answers[id] = state.stale;
        state.stale = null;
        state.versions[id] = (state.versions[id] ?? 1) + 1;
        return HttpResponse.json(
          {
            detail:
              "The form or your documents changed since you looked. Check the answers shown now and approve again.",
            code: "stale",
            answers_hash: state.hash(id),
            answers: state.answers[id],
          },
          { status: 409 },
        );
      }
      const refusal = refused();
      if (refusal) return refusal;
      state.items = state.items.map((item) =>
        item.id === id ? { ...item, state: "approved" } : item,
      );
      return HttpResponse.json(
        fakeApplication("approved", { id, posting_id: id }),
      );
    }),
    http.post("*/api/applications/:id/cancel", ({ params }) => {
      const id = String(params.id);
      state.cancels.push(id);
      const refusal = refused();
      if (refusal) return refusal;
      take(id);
      return HttpResponse.json(fakeApplication("cancelled", { id }));
    }),
  );
  return state;
}
