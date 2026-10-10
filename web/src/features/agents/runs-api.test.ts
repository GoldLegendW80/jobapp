import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/server";

import { FakeEventSource } from "./fake-event-source";
import {
  followRun,
  followTiming,
  type Connection,
  type RunStep,
} from "./runs-api";

const ID = "11111111-1111-4111-8111-111111111111";

function step(seq: number, kind = "text", data: Record<string, unknown> = {}) {
  return { seq, at: "2026-10-10T08:00:00Z", kind, data };
}

function detail(status = "running") {
  return {
    id: ID,
    agent: "echo",
    job_id: null,
    status,
    input: { message: "hi" },
    output: null,
    error: null,
    model: "claude-opus-5-5",
    input_tokens: null,
    output_tokens: null,
    cost_usd: null,
    cancel_requested: false,
    created_at: "2026-10-10T08:00:00Z",
    started_at: "2026-10-10T08:00:01Z",
    finished_at: null,
    duration_s: null,
    events: [],
    events_truncated: false,
  };
}

function follow(after?: number) {
  const steps: RunStep[] = [];
  const states: Connection[] = [];
  const gone = vi.fn();
  const stop = followRun(
    ID,
    {
      onStep: (s) => steps.push(s),
      onConnection: (s) => states.push(s),
      onGone: gone,
    },
    after === undefined ? {} : { after },
  );
  return { steps, states, gone, stop };
}

beforeEach(() => {
  vi.stubGlobal("EventSource", FakeEventSource);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  FakeEventSource.reset();
});

describe("followRun", () => {
  it("streams steps in order, never twice, and stops at end", () => {
    const { steps, states } = follow();
    const source = FakeEventSource.last!;
    expect(source.url).toBe(`/api/agents/runs/${ID}/stream?after=0`);
    expect(states).toEqual(["connecting"]);
    source.open();
    source.emit("step", step(1, "status", { status: "queued" }));
    source.emit("step", step(2));
    source.emit("step", step(2)); // a duplicate after a reconnect
    source.emit("step", step(1));
    source.emit("step", { not: "a step" });
    source.emit("step", "{broken json");
    source.emit("step", step(3));
    source.emit("end", {});
    expect(steps.map((s) => s.seq)).toEqual([1, 2, 3]);
    expect(states).toEqual(["connecting", "live", "ended"]);
    expect(source.closed).toBe(true);
    source.emit("step", step(4));
    expect(steps).toHaveLength(3);
  });

  it("resumes after the given step", () => {
    follow(7);
    expect(FakeEventSource.last!.url).toMatch(/\?after=7$/);
  });

  it("lets the browser retry a dropped connection by itself", () => {
    const { steps, states } = follow();
    const source = FakeEventSource.last!;
    source.open();
    source.emit("step", step(1));
    source.fail(false);
    expect(states.at(-1)).toBe("reconnecting");
    expect(FakeEventSource.all).toHaveLength(1);
    source.open(); // the browser reconnected, sending Last-Event-ID
    source.emit("step", step(2));
    expect(states.at(-1)).toBe("live");
    expect(steps.map((s) => s.seq)).toEqual([1, 2]);
  });

  it("reconnects by itself from the last step when the browser gave up", async () => {
    vi.useFakeTimers();
    server.use(
      http.get(`*/api/agents/runs/${ID}`, () => HttpResponse.json(detail())),
    );
    const { steps } = follow();
    const first = FakeEventSource.last!;
    first.open();
    first.emit("step", step(1));
    first.emit("step", step(2));
    first.fail(true);
    expect(FakeEventSource.all).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(followTiming.retryMs + 50);
    await vi.waitFor(() => {
      expect(FakeEventSource.all).toHaveLength(2);
    });
    const second = FakeEventSource.last!;
    expect(second.url).toMatch(/\?after=2$/);
    // A late event of the old connection is ignored.
    first.emit("step", step(9));
    second.open();
    second.emit("step", step(3));
    expect(steps.map((s) => s.seq)).toEqual([1, 2, 3]);
  });

  it("backs off between failed reconnects", async () => {
    vi.useFakeTimers();
    server.use(
      http.get(`*/api/agents/runs/${ID}`, () =>
        HttpResponse.json({ detail: "down" }, { status: 502 }),
      ),
    );
    follow();
    FakeEventSource.last!.fail(true);
    await vi.advanceTimersByTimeAsync(followTiming.retryMs + 50);
    await vi.waitFor(() => {
      expect(FakeEventSource.all).toHaveLength(2);
    });
    FakeEventSource.last!.fail(true);
    await vi.advanceTimersByTimeAsync(followTiming.retryMs + 50);
    expect(FakeEventSource.all).toHaveLength(2); // waits twice as long now
    await vi.advanceTimersByTimeAsync(followTiming.retryMs);
    await vi.waitFor(() => {
      expect(FakeEventSource.all).toHaveLength(3);
    });
  });

  it("ends when the run is gone", async () => {
    vi.useFakeTimers();
    server.use(
      http.get(`*/api/agents/runs/${ID}`, () =>
        HttpResponse.json({ detail: "Run not found." }, { status: 404 }),
      ),
    );
    const { gone, states } = follow();
    FakeEventSource.last!.fail(true);
    await vi.advanceTimersByTimeAsync(followTiming.retryMs + 50);
    await vi.waitFor(() => {
      expect(gone).toHaveBeenCalledOnce();
    });
    expect(states.at(-1)).toBe("ended");
    expect(FakeEventSource.all).toHaveLength(1);

    const other = follow();
    FakeEventSource.last!.emit("gone", {});
    expect(other.gone).toHaveBeenCalledOnce();
  });

  it("stops when asked, even with a reconnect pending", async () => {
    vi.useFakeTimers();
    const { stop, steps } = follow();
    const source = FakeEventSource.last!;
    source.fail(true);
    stop();
    await vi.advanceTimersByTimeAsync(followTiming.maxRetryMs);
    expect(FakeEventSource.all).toHaveLength(1);
    source.emit("step", step(1));
    expect(steps).toEqual([]);
  });
});
