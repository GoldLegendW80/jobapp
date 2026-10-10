import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { admin, fakeSession } from "@/test/auth";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import { FakeEventSource } from "@/test/fake-event-source";
import type { Run } from "./runs-api";

const JOB = "00000000-0000-4000-9000-000000000007";
const ON_JOB = "77777777-7777-4777-8777-000000000001";
const GONE = "77777777-7777-4777-8777-000000000002";
const ALONE = "77777777-7777-4777-8777-000000000003";

function run(id: string, changes: Partial<Run> = {}): Run {
  return {
    id,
    agent: "triage",
    job_id: null,
    status: "done",
    input: {},
    output: null,
    error: null,
    model: "claude-opus-5-5",
    input_tokens: 100,
    output_tokens: 10,
    cost_usd: 0.01,
    cancel_requested: false,
    created_at: "2026-10-10T08:00:00Z",
    started_at: "2026-10-10T08:00:01Z",
    finished_at: "2026-10-10T08:00:05Z",
    duration_s: 4,
    job: null,
    ...changes,
  };
}

const runs = [
  run(ON_JOB, {
    job_id: JOB,
    job: { id: JOB, company: "Mistral AI", title: "Applied AI Engineer" },
  }),
  run(GONE, { job_id: "00000000-0000-4000-9000-000000000099", job: null }),
  run(ALONE, { agent: "echo" }),
];

beforeEach(() => {
  vi.stubGlobal("EventSource", FakeEventSource);
  fakeSession(admin);
  server.use(
    http.get("*/api/agents", () => HttpResponse.json({ items: [] })),
    http.get("*/api/agents/runs", () => HttpResponse.json({ items: runs })),
    http.get(`*/api/agents/runs/${ON_JOB}`, () =>
      HttpResponse.json({ ...runs[0], events: [], events_truncated: false }),
    ),
  );
});

afterEach(() => {
  FakeEventSource.reset();
  vi.unstubAllGlobals();
});

describe("runs and their job (A3)", () => {
  it("links each run on a job to that job, outside the run's own link", async () => {
    renderApp("/runs");
    const list = await screen.findByRole("list", { name: "Runs" });
    const rows = within(list).getAllByRole("listitem");
    const job = within(rows[0]!).getByRole("link", {
      name: "Applied AI Engineer at Mistral AI",
    });
    expect(job).toHaveAttribute("href", `/jobs/${JOB}`);
    expect(job.closest("a[href^='/runs/']")).toBeNull();
    expect(rows[1]).toHaveTextContent("On a job that no longer exists");
    expect(within(rows[2]!).getAllByRole("link")).toHaveLength(1);
  });

  it("shows the job link on the run's page", async () => {
    renderApp(`/runs/${ON_JOB}`);
    expect(
      await screen.findByRole("link", {
        name: "Applied AI Engineer at Mistral AI",
      }),
    ).toHaveAttribute("href", `/jobs/${JOB}`);
  });
});
