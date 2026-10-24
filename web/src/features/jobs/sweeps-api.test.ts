import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/server";

import { sweepSchema, watchSweep, type Sweep } from "./sweeps-api";

const ID = "11111111-2222-4333-8444-555555555555";

const running: Sweep = {
  id: ID,
  trigger: "manual",
  status: "running",
  started_at: "2026-10-09T08:00:00Z",
  finished_at: null,
  error: null,
  boards_total: 2,
  boards_done: 1,
  boards_ok: 1,
  boards_failed: [],
  current_board: "Acme",
  seen: 10,
  matched: 2,
  new: 1,
  duplicates: 0,
  skipped_by_rule: 0,
  duration: null,
  sources: [],
};
const done = {
  ...running,
  status: "done",
  boards_done: 2,
  current_board: null,
  new_jobs: [],
};

it.each([
  {},
  { source_id: null, mode: null, phase: null, outcome: null, reason: null },
  {
    source_id: "board:example",
    mode: "api",
    phase: "done",
    outcome: "complete",
    reason: null,
  },
  {
    source_id: "site:example",
    mode: "llm_browser",
    phase: "pending",
    outcome: "not_attempted",
    reason: "interactive_executor_pending",
  },
  { mode: "feed", phase: "done", outcome: "no_matches" },
])("preserves optional source status metadata: %j", (metadata) => {
  const source = {
    kind: "google_careers",
    query: "ai",
    location: null,
    result_pages: 0,
    found: 0,
    new: 0,
    error: null,
    ...metadata,
  };
  expect(sweepSchema.parse({ ...running, sources: [source] }).sources).toEqual([
    { ...source, skipped_by_rule: 0 },
  ]);
});

/** A stand-in for the browser's EventSource that the test drives. */
class FakeEventSource {
  static last: FakeEventSource | undefined;
  url: string;
  closed = false;
  onerror: (() => void) | null = null;
  listeners = new Map<string, (event: MessageEvent<string>) => void>();
  constructor(url: string) {
    this.url = url;
    FakeEventSource.last = this;
  }
  addEventListener(name: string, fn: (event: MessageEvent<string>) => void) {
    this.listeners.set(name, fn);
  }
  emit(name: string, data: unknown) {
    this.listeners.get(name)?.({
      data: JSON.stringify(data),
    } as MessageEvent<string>);
  }
  close() {
    this.closed = true;
  }
}

afterEach(() => {
  vi.unstubAllGlobals();
  FakeEventSource.last = undefined;
});

describe("watchSweep over server-sent events", () => {
  it("passes progress and the final state, then closes the stream", () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    const progress = vi.fn();
    const finished = vi.fn();
    watchSweep(ID, {
      onProgress: progress,
      onDone: finished,
      onError: vi.fn(),
    });
    const source = FakeEventSource.last!;
    expect(source.url).toBe(`/api/jobs/sweeps/${ID}/events`);
    source.emit("progress", running);
    source.emit("progress", { not: "a sweep" });
    source.emit("done", done);
    expect(progress).toHaveBeenCalledTimes(1);
    expect(finished).toHaveBeenCalledWith(done);
    expect(source.closed).toBe(true);
    source.emit("progress", running);
    expect(progress).toHaveBeenCalledTimes(1);
  });

  it("falls back to polling when the stream fails", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    server.use(
      http.get("*/api/jobs/sweeps/:id", () => HttpResponse.json(done)),
    );
    const finished = vi.fn();
    watchSweep(
      ID,
      { onProgress: vi.fn(), onDone: finished, onError: vi.fn() },
      { pollMs: 5 },
    );
    const source = FakeEventSource.last!;
    source.onerror?.();
    expect(source.closed).toBe(true);
    await vi.waitFor(() => {
      expect(finished).toHaveBeenCalledWith(done);
    });
  });

  it("reports a sweep that is gone", () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    const failed = vi.fn();
    watchSweep(ID, { onProgress: vi.fn(), onDone: vi.fn(), onError: failed });
    FakeEventSource.last!.emit("gone", {});
    expect(failed).toHaveBeenCalledTimes(1);
    expect(FakeEventSource.last!.closed).toBe(true);
  });

  it("stops on demand", () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    const finished = vi.fn();
    const stop = watchSweep(ID, {
      onProgress: vi.fn(),
      onDone: finished,
      onError: vi.fn(),
    });
    stop();
    FakeEventSource.last!.emit("done", done);
    expect(finished).not.toHaveBeenCalled();
  });
});
