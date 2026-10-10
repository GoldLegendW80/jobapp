import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { JobDetail } from "@/features/jobs/api";
import { admin, fakeSession } from "@/test/auth";
import { makeDetail } from "@/test/jobs";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import { FakeEventSource } from "./fake-event-source";
import type { Proposal } from "./proposals-api";
import type { Run } from "./runs-api";

const JOB = "00000000-0000-4000-9000-000000000001";
const RUN = "44444444-4444-4444-8444-444444444444";
const VERSION = "2026-10-09T08:00:00Z";

function run(changes: Partial<Run> = {}): Run {
  return {
    id: RUN,
    agent: "triage",
    job_id: JOB,
    status: "done",
    input: {},
    output: { text: "A strong fit." },
    error: null,
    model: "claude-opus-5-5",
    input_tokens: 16700,
    output_tokens: 640,
    cost_usd: 0.0874,
    cancel_requested: false,
    created_at: "2026-10-10T08:00:00Z",
    started_at: "2026-10-10T08:00:01Z",
    finished_at: "2026-10-10T08:00:09Z",
    duration_s: 8,
    job: { id: JOB, company: "Mistral AI", title: "Engineer 1" },
    ...changes,
  };
}

function proposal(
  id: string,
  kind: "job_note" | "job_status",
  changes: Partial<Proposal> = {},
): Proposal {
  return {
    id,
    run_id: RUN,
    target: "job",
    target_id: JOB,
    kind,
    payload:
      kind === "job_note"
        ? { text: "Strong fit: agents and evaluation." }
        : {
            status: "shortlisted",
            from: "found",
            reason: "Strong fit on agents.",
          },
    status: "pending",
    decided_by: null,
    decided_at: null,
    edited_value: null,
    reason: null,
    created_at: "2026-10-10T08:00:05Z",
    ...changes,
  };
}

const NOTE = "55555555-5555-4555-8555-000000000001";
const STATUS = "55555555-5555-4555-8555-000000000002";

const credential = {
  kind: "anthropic_api_key",
  last4: "Rn42",
  verified_at: "2026-10-10T07:00:00Z",
  updated_at: "2026-10-10T07:00:00Z",
};

/** A fake api for job detail with the agent: the job, the credential, runs and proposals. */
function fakeApi(
  options: {
    credential?: boolean;
    runs?: Run[];
    proposals?: Proposal[];
    job?: JobDetail;
  } = {},
) {
  fakeSession(admin);
  const state = {
    job: options.job ?? makeDetail({ updated_at: VERSION }),
    runs: options.runs ?? [],
    proposals: options.proposals ?? [],
    starts: [] as unknown[],
    accepts: [] as Array<{ id: string; body: Record<string, unknown> }>,
    rejects: [] as string[],
    jobReads: 0,
    acceptAnswer: null as Response | null,
  };
  server.use(
    http.get("*/api/jobs/:id", () => {
      state.jobReads += 1;
      return HttpResponse.json(state.job);
    }),
    http.get("*/api/me/model-credential", () =>
      options.credential === false
        ? HttpResponse.json({ detail: "none" }, { status: 404 })
        : HttpResponse.json(credential),
    ),
    http.get("*/api/agents/runs", ({ request }) => {
      const url = new URL(request.url);
      expect(url.searchParams.get("job_id")).toBe(JOB);
      expect(url.searchParams.get("agent")).toBe("triage");
      return HttpResponse.json({ items: state.runs });
    }),
    http.get(`*/api/agents/runs/${RUN}`, () =>
      HttpResponse.json({
        ...(state.runs[0] ?? run()),
        events: [],
        events_truncated: false,
      }),
    ),
    http.post("*/api/agents/runs", async ({ request }) => {
      state.starts.push(await request.json());
      const started = run({ status: "queued", output: null });
      state.runs = [started];
      return HttpResponse.json(started, { status: 202 });
    }),
    http.get("*/api/proposals", ({ request }) => {
      const url = new URL(request.url);
      expect(url.searchParams.get("target_id")).toBe(JOB);
      return HttpResponse.json({ items: state.proposals });
    }),
    http.post("*/api/proposals/:id/accept", async ({ params, request }) => {
      expect(request.headers.get("X-Requested-With")).toBe("jobapp");
      const body = (await request.json()) as Record<string, unknown>;
      state.accepts.push({ id: String(params.id), body });
      if (state.acceptAnswer) return state.acceptAnswer;
      state.proposals = state.proposals.map((p) =>
        p.id === params.id
          ? {
              ...p,
              status: "accepted",
              decided_by: "user",
              decided_at: "2026-10-10T08:01:00Z",
              edited_value: body.edited_value ?? null,
            }
          : p,
      );
      return HttpResponse.json(state.proposals.find((p) => p.id === params.id));
    }),
    http.post("*/api/proposals/:id/reject", ({ params }) => {
      state.rejects.push(String(params.id));
      state.proposals = state.proposals.map((p) =>
        p.id === params.id
          ? {
              ...p,
              status: "rejected",
              decided_by: "user",
              decided_at: "2026-10-10T08:01:00Z",
            }
          : p,
      );
      return HttpResponse.json(state.proposals.find((p) => p.id === params.id));
    }),
  );
  return state;
}

beforeEach(() => {
  vi.stubGlobal("EventSource", FakeEventSource);
});

afterEach(() => {
  FakeEventSource.reset();
  vi.unstubAllGlobals();
});

async function openJob() {
  renderApp(`/jobs/${JOB}`);
  return screen.findByRole("region", { name: "Agent assessment" });
}

const assessButton = () =>
  screen.getByRole("button", { name: /Assess with agent|Assessing/ });

describe("job detail: agent assessment", () => {
  it("explains a missing credential and disables the button", async () => {
    fakeApi({ credential: false });
    const section = await openJob();
    const explanation = await within(section).findByText(/none is saved yet/);
    expect(assessButton()).toBeDisabled();
    expect(assessButton()).toHaveAccessibleDescription(
      /Agents run with your own model credential/,
    );
    expect(
      within(explanation).getByRole("link", {
        name: "Add one on the Account page",
      }),
    ).toHaveAttribute("href", "/account");
    expect(document.body.textContent).not.toMatch(/[–—]| - /);
  });

  it("starts the triage agent on this job and shows the run live", async () => {
    const state = fakeApi();
    const user = userEvent.setup();
    const section = await openJob();
    await waitFor(() => {
      expect(assessButton()).toBeEnabled();
    });
    expect(within(section).queryByTestId("run-panel")).not.toBeInTheDocument();
    await user.click(assessButton());
    await waitFor(() => {
      expect(state.starts).toEqual([
        { agent: "triage", job_id: JOB, input: {} },
      ]);
    });
    const panel = await within(section).findByTestId("run-panel");
    expect(within(panel).getByText("Triage agent")).toBeInTheDocument();
    expect(assessButton()).toHaveTextContent("Assessing");
    expect(assessButton()).toBeDisabled();
    await waitFor(() => {
      expect(FakeEventSource.last?.url).toContain(
        `/api/agents/runs/${RUN}/stream`,
      );
    });
  });

  it("finds the latest run again after a reload, with its proposals", async () => {
    fakeApi({
      runs: [run()],
      proposals: [proposal(NOTE, "job_note"), proposal(STATUS, "job_status")],
    });
    const section = await openJob();
    expect(await within(section).findByTestId("run-panel")).toBeInTheDocument();
    const list = await within(section).findByRole("list", {
      name: "Proposals",
    });
    const cards = within(list).getAllByTestId("proposal");
    expect(cards).toHaveLength(2);
    expect(cards[0]).toHaveTextContent("Note");
    expect(cards[0]).toHaveTextContent("Strong fit: agents and evaluation.");
    expect(cards[1]).toHaveTextContent("Status: Found to Shortlisted");
    expect(cards[1]).toHaveTextContent("Strong fit on agents.");
    // Edit is offered for a note only.
    expect(
      within(cards[1]!).queryByRole("button", { name: /Edit/ }),
    ).not.toBeInTheDocument();
    expect(assessButton()).toHaveTextContent("Assess with agent");
  });

  it("accepts a note with the job's version, then the job reloads", async () => {
    const state = fakeApi({
      runs: [run()],
      proposals: [proposal(NOTE, "job_note")],
    });
    const user = userEvent.setup();
    const section = await openJob();
    const accept = await within(section).findByRole("button", {
      name: "Accept the note",
    });
    const readsBefore = state.jobReads;
    await user.click(accept);
    await waitFor(() => {
      expect(state.accepts).toEqual([
        { id: NOTE, body: { updated_at: VERSION } },
      ]);
    });
    expect(await screen.findByText("Note added to the job.")).toBeVisible();
    await waitFor(() => {
      expect(state.jobReads).toBeGreaterThan(readsBefore);
    });
    const card = await within(section).findByTestId("proposal");
    await waitFor(() => {
      expect(card).toHaveAttribute("data-status", "accepted");
    });
    expect(card).toHaveTextContent("Accepted");
    expect(
      within(card).queryByRole("button", { name: /Accept/ }),
    ).not.toBeInTheDocument();
  });

  it("edits a note, then accepts the edited text", async () => {
    const state = fakeApi({
      runs: [run()],
      proposals: [proposal(NOTE, "job_note")],
    });
    const user = userEvent.setup();
    const section = await openJob();
    await user.click(
      await within(section).findByRole("button", {
        name: "Edit the note, then accept",
      }),
    );
    const box = within(section).getByLabelText("Edit the note");
    expect(box).toHaveValue("Strong fit: agents and evaluation.");
    await user.clear(box);
    const save = within(section).getByRole("button", {
      name: "Accept edited note",
    });
    expect(save).toBeDisabled();
    await user.type(box, "  My own words 👍  ");
    await user.click(save);
    await waitFor(() => {
      expect(state.accepts).toEqual([
        {
          id: NOTE,
          body: { updated_at: VERSION, edited_value: "My own words 👍" },
        },
      ]);
    });
    await waitFor(() => {
      expect(within(section).getByTestId("proposal")).toHaveTextContent(
        "My own words 👍",
      );
    });
  });

  it("cancels an edit without sending anything", async () => {
    const state = fakeApi({
      runs: [run()],
      proposals: [proposal(NOTE, "job_note")],
    });
    const user = userEvent.setup();
    const section = await openJob();
    await user.click(
      await within(section).findByRole("button", {
        name: "Edit the note, then accept",
      }),
    );
    await user.type(within(section).getByLabelText("Edit the note"), " more");
    await user.click(within(section).getByRole("button", { name: "Cancel" }));
    expect(
      within(section).queryByLabelText("Edit the note"),
    ).not.toBeInTheDocument();
    expect(state.accepts).toEqual([]);
  });

  it("rejects a status change", async () => {
    const state = fakeApi({
      runs: [run()],
      proposals: [proposal(STATUS, "job_status")],
    });
    const user = userEvent.setup();
    const section = await openJob();
    await user.click(
      await within(section).findByRole("button", {
        name: "Reject the status change",
      }),
    );
    await waitFor(() => {
      expect(state.rejects).toEqual([STATUS]);
    });
    expect(await screen.findByText("Proposal rejected.")).toBeVisible();
    await waitFor(() => {
      expect(within(section).getByTestId("proposal")).toHaveAttribute(
        "data-status",
        "rejected",
      );
    });
  });

  it("accepts a status change and names the new status", async () => {
    const state = fakeApi({
      runs: [run()],
      proposals: [proposal(STATUS, "job_status")],
    });
    const user = userEvent.setup();
    const section = await openJob();
    await user.click(
      await within(section).findByRole("button", {
        name: "Accept the status change",
      }),
    );
    expect(
      await screen.findByText("Status changed to Shortlisted."),
    ).toBeVisible();
    expect(state.accepts[0]?.body).toEqual({ updated_at: VERSION });
  });

  it("says when the job changed meanwhile, and reloads it", async () => {
    const state = fakeApi({
      runs: [run()],
      proposals: [proposal(NOTE, "job_note")],
    });
    state.acceptAnswer = HttpResponse.json(
      {
        detail: "This job changed since you loaded it.",
        reason: "stale",
        status: null,
        updated_at: "2026-10-10T09:00:00Z",
      },
      { status: 409 },
    );
    const user = userEvent.setup();
    const section = await openJob();
    const reads = state.jobReads;
    await user.click(
      await within(section).findByRole("button", { name: "Accept the note" }),
    );
    expect(
      await screen.findByText(/This job changed since it was loaded/),
    ).toBeVisible();
    await waitFor(() => {
      expect(state.jobReads).toBeGreaterThan(reads);
    });
    expect(within(section).getByTestId("proposal")).toHaveAttribute(
      "data-status",
      "pending",
    );
  });

  it("shows the server's reason for other refusals", async () => {
    const state = fakeApi({
      runs: [run()],
      proposals: [proposal(NOTE, "job_note")],
    });
    state.acceptAnswer = HttpResponse.json(
      {
        detail: "This proposal was already decided: it is superseded.",
        reason: "not_pending",
        status: "superseded",
        updated_at: null,
      },
      { status: 409 },
    );
    const user = userEvent.setup();
    const section = await openJob();
    await user.click(
      await within(section).findByRole("button", { name: "Accept the note" }),
    );
    expect(
      await screen.findByText(
        "This proposal was already decided: it is superseded.",
      ),
    ).toBeVisible();
  });

  it("lists decided proposals of the latest run only, and says when it proposed nothing", async () => {
    fakeApi({
      runs: [run()],
      proposals: [
        proposal(NOTE, "job_note", {
          status: "superseded",
          decided_by: "system",
          decided_at: "2026-10-10T08:00:06Z",
        }),
        proposal("55555555-5555-4555-8555-000000000003", "job_note", {
          run_id: "66666666-6666-4666-8666-666666666666",
          status: "rejected",
          decided_by: "user",
          decided_at: "2026-10-09T08:00:06Z",
        }),
      ],
    });
    const section = await openJob();
    const card = await within(section).findByTestId("proposal");
    expect(card).toHaveTextContent("Replaced by a newer one");
    expect(within(section).getAllByTestId("proposal")).toHaveLength(1);
  });

  it("says when the last assessment proposed nothing", async () => {
    fakeApi({ runs: [run()], proposals: [] });
    const section = await openJob();
    expect(
      await within(section).findByText("The last assessment proposed nothing."),
    ).toBeVisible();
  });

  it("tells why a start was refused", async () => {
    fakeApi();
    server.use(
      http.post("*/api/agents/runs", () =>
        HttpResponse.json(
          {
            detail:
              "No model credential is saved. Add one on the Account page first.",
          },
          { status: 409 },
        ),
      ),
    );
    const user = userEvent.setup();
    await openJob();
    await waitFor(() => {
      expect(assessButton()).toBeEnabled();
    });
    await user.click(assessButton());
    expect(
      await screen.findByText(/The assessment did not start\. No model/),
    ).toBeVisible();
  });

  it("shows an accepted agent change in the history", async () => {
    fakeApi({
      job: makeDetail({
        updated_at: VERSION,
        events: [
          {
            id: 2,
            at: "2026-10-10T08:01:00Z",
            kind: "status",
            data: {
              from: "found",
              to: "skipped",
              via: "agent",
              proposal_id: STATUS,
            },
          },
          {
            id: 3,
            at: "2026-10-10T08:02:00Z",
            kind: "note",
            data: { from: "", to: "x", via: "agent", proposal_id: NOTE },
          },
        ],
        events_total: 2,
      }),
    });
    await openJob();
    const history = screen.getByRole("list", { name: "History, newest first" });
    expect(history).toHaveTextContent(
      "Status changed from Found to Skipped (agent proposal accepted)",
    );
    expect(history).toHaveTextContent("Note added (agent proposal accepted)");
  });
});
