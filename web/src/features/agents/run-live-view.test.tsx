import { QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  browserQueryKey,
  type Browser,
  type Lease,
} from "@/features/browser/browser-api";
import { createQueryClient } from "@/lib/query";
import { routes } from "@/routes";
import { admin, fakeSession } from "@/test/auth";
import { FREE, connected, fakeScreens } from "@/test/browser";
import { FakeEventSource } from "@/test/fake-event-source";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import {
  ELSEWHERE,
  STARTING,
  WEBSITE_AGENT,
  browserOfRun,
} from "./run-browser";
import type { Run, RunStatus } from "./runs-api";

const ID = "22222222-2222-4222-8222-222222222222";
const LEASE_ID = "3c5d7e9f-1a2b-4c3d-8e4f-5a6b7c8d9e0f";
// The lease starts before the run does (the search takes its lease first).
const LEASE_STARTED = "2026-10-29T09:00:00Z";
const RUN_STARTED = "2026-10-29T09:00:03Z";
const AFTER_RUN = "2026-10-29T09:05:00Z";

function run(changes: Partial<Run> = {}): Run {
  return {
    id: ID,
    agent: WEBSITE_AGENT,
    job_id: null,
    status: "running",
    input: {},
    output: null,
    error: null,
    model: "claude-opus-5-5",
    input_tokens: null,
    output_tokens: null,
    cost_usd: null,
    cancel_requested: false,
    created_at: "2026-10-29T09:00:02Z",
    started_at: RUN_STARTED,
    finished_at: null,
    duration_s: null,
    ...changes,
  };
}

function lease(changes: Partial<Lease> = {}): Lease {
  return {
    id: LEASE_ID,
    purpose: "sweep",
    automation: true,
    state: "active",
    wait_reason: null,
    wait_host: null,
    wait_deadline: null,
    started_at: LEASE_STARTED,
    active_s: 12,
    max_s: 900,
    ...changes,
  };
}

const mine = (found: Lease | null): Browser => ({
  ...FREE,
  lease: found,
  holder: found ? "you" : null,
});

/** What the run page asked of /api/browser: `calls` as "METHOD path", `bodies` of take over and give back. */
interface Calls {
  calls: string[];
  bodies: Array<{ viewer: string }>;
}

/** The fake api of the run page: the run, the person's browser, every call to /api/browser. */
function fakeApi(state: { detail: Run; browser: Browser }): Calls {
  fakeSession(admin);
  const seen: Calls = { calls: [], bodies: [] };
  const note = (request: Request) => {
    seen.calls.push(`${request.method} ${new URL(request.url).pathname}`);
  };
  server.use(
    http.get(`*/api/agents/runs/${ID}`, () =>
      HttpResponse.json({
        ...state.detail,
        events: [],
        events_truncated: false,
      }),
    ),
    http.get("*/api/browser", ({ request }) => {
      note(request);
      return HttpResponse.json(state.browser);
    }),
    http.post("*/api/browser/takeover", async ({ request }) => {
      note(request);
      seen.bodies.push((await request.json()) as { viewer: string });
      return HttpResponse.json({ input: "you" });
    }),
    http.post("*/api/browser/giveback", async ({ request }) => {
      note(request);
      seen.bodies.push((await request.json()) as { viewer: string });
      return HttpResponse.json({ input: "assistant" });
    }),
    // Any other call to /api/browser is noted, then goes on to the next handler (the live ticket).
    http.all("*/api/browser/*", ({ request }) => {
      note(request);
    }),
  );
  return seen;
}

beforeEach(() => {
  vi.stubGlobal("EventSource", FakeEventSource);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  FakeEventSource.reset();
});

/** The page has the run: its panel is there. */
async function panel() {
  await screen.findByTestId("run-panel");
}

describe("a running website search", () => {
  it("shows the live view of the sweep lease, watching, with Take over", async () => {
    const opened = fakeScreens();
    const { calls } = fakeApi({ detail: run(), browser: mine(lease()) });
    renderApp(`/runs/${ID}`);
    const view = await connected(opened);
    await panel();
    expect(view.screen.viewOnly).toBe(true);
    expect(await screen.findByTestId("run-live-view")).toContainElement(
      screen.getByTestId("live-screen"),
    );
    expect(
      await screen.findByRole("button", { name: "Take over" }),
    ).toBeEnabled();
    expect(
      screen.getByText(
        "You are watching your browser. Take over to use it yourself.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(STARTING)).toBeNull();
    expect(screen.queryByText(ELSEWHERE)).toBeNull();
    // It only reads the browser and asks for the view's ticket: nothing starts, ends or takes
    // a lease, and nothing is typed or taken.
    expect([...new Set(calls)].sort()).toEqual([
      "GET /api/browser",
      "POST /api/browser/live/ticket",
    ]);
  });

  it("takes the keyboard and gives it back as the Account card does", async () => {
    const opened = fakeScreens();
    const { calls, bodies } = fakeApi({
      detail: run(),
      browser: mine(lease()),
    });
    const user = userEvent.setup();
    renderApp(`/runs/${ID}`);
    const view = await connected(opened);
    await user.click(await screen.findByRole("button", { name: "Take over" }));
    expect(
      await screen.findByRole("button", { name: "Give back" }),
    ).toBeVisible();
    expect(view.screen.viewOnly).toBe(false);
    await user.click(screen.getByRole("button", { name: "Give back" }));
    await waitFor(() => {
      expect(view.screen.viewOnly).toBe(true);
    });
    // The same viewer (this tab's view) takes and gives back; no lease is started or ended.
    expect(bodies).toHaveLength(2);
    expect(bodies[0]?.viewer).toMatch(/^[0-9a-f-]{36}$/);
    expect(bodies[1]).toEqual(bodies[0]);
    expect(calls.filter((call) => /takeover|giveback/.test(call))).toEqual([
      "POST /api/browser/takeover",
      "POST /api/browser/giveback",
    ]);
    expect(
      calls.filter((call) => /sessions|handoff|profile/.test(call)),
    ).toEqual([]);
  });

  it("says the browser is starting while the lease starts, then shows it", async () => {
    const opened = fakeScreens();
    const state = {
      detail: run(),
      browser: mine(lease({ state: "starting" })),
    };
    fakeApi(state);
    const { client } = renderApp(`/runs/${ID}`);
    expect(await screen.findByText(STARTING)).toBeInTheDocument();
    expect(screen.queryByTestId("live-screen")).toBeNull();
    expect(opened).toHaveLength(0);
    expect(screen.queryByText(ELSEWHERE)).toBeNull();
    state.browser = mine(lease());
    await act(() => client.invalidateQueries({ queryKey: browserQueryKey }));
    await connected(opened);
    expect(screen.queryByText(STARTING)).toBeNull();
    expect(screen.getByTestId("live-screen")).toBeInTheDocument();
  });

  it("says the browser is starting before the search has its lease", async () => {
    const opened = fakeScreens();
    fakeApi({ detail: run(), browser: mine(null) });
    renderApp(`/runs/${ID}`);
    expect(await screen.findByText(STARTING)).toBeInTheDocument();
    await panel();
    expect(screen.queryByText(ELSEWHERE)).toBeNull();
    expect(opened).toHaveLength(0);
  });

  it("reads the browser again every 3 seconds", async () => {
    fakeScreens();
    fakeApi({ detail: run(), browser: mine(lease()) });
    const { client } = renderApp(`/runs/${ID}`);
    await panel();
    await waitFor(() => {
      expect(
        client.getQueryCache().find({ queryKey: browserQueryKey })?.observers[0]
          ?.options.refetchInterval,
      ).toBe(3000);
    });
  });

  it("does not trust the cache: the lease of the website before is not shown", async () => {
    const opened = fakeScreens();
    const state = { detail: run(), browser: mine(lease({ id: LEASE_ID })) };
    fakeApi(state);
    let answer: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      answer = resolve;
    });
    server.use(
      http.get("*/api/browser", async () => {
        await gate;
        return HttpResponse.json(state.browser);
      }),
    );
    // The browser module's cache still holds the lease of the website searched before.
    const client = createQueryClient();
    client.setQueryData(browserQueryKey, mine(lease({ id: "old-website" })));
    const router = createMemoryRouter(routes, {
      initialEntries: [`/runs/${ID}`],
    });
    render(
      <QueryClientProvider client={client}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    await panel();
    expect(await screen.findByText(STARTING)).toBeInTheDocument();
    expect(opened).toHaveLength(0);
    answer();
    await connected(opened);
    expect(screen.queryByText(STARTING)).toBeNull();
  });

  it("closes the view when the run ends", async () => {
    const opened = fakeScreens();
    const state = { detail: run(), browser: mine(lease()) };
    fakeApi(state);
    renderApp(`/runs/${ID}`);
    const view = await connected(opened);
    await panel();
    state.detail = run({ status: "done", finished_at: AFTER_RUN });
    act(() => {
      FakeEventSource.last?.emit("step", {
        seq: 1,
        at: AFTER_RUN,
        kind: "status",
        data: { status: "done" },
      });
    });
    expect(await screen.findByText(ELSEWHERE)).toBeInTheDocument();
    expect(screen.queryByTestId("live-screen")).toBeNull();
    expect(view.screen.disconnected).toBe(true);
  });
});

describe("a website search that is not running", () => {
  it("says the browser is starting while it is queued, and reads nothing", async () => {
    const opened = fakeScreens();
    const { calls } = fakeApi({
      detail: run({ status: "queued", started_at: null }),
      browser: mine(lease()),
    });
    renderApp(`/runs/${ID}`);
    expect(await screen.findByText(STARTING)).toBeInTheDocument();
    await panel();
    expect(screen.queryByText(ELSEWHERE)).toBeNull();
    expect(opened).toHaveLength(0);
    expect(calls).toEqual([]);
  });

  it.each(["done", "failed", "cancelled"] as const)(
    "shows one line and no view once it is %s, though the lease is still the sweep's",
    async (status: RunStatus) => {
      const opened = fakeScreens();
      const { calls } = fakeApi({
        detail: run({ status, finished_at: AFTER_RUN }),
        browser: mine(lease()),
      });
      renderApp(`/runs/${ID}`);
      expect(await screen.findByText(ELSEWHERE)).toBeInTheDocument();
      await panel();
      expect(screen.queryByText(STARTING)).toBeNull();
      expect(screen.queryByTestId("live-screen")).toBeNull();
      expect(opened).toHaveLength(0);
      expect(calls).toEqual([]);
    },
  );
});

describe("a running website search whose browser is not its lease", () => {
  it.each([
    ["another purpose", mine(lease({ purpose: "apply_fill" }))],
    ["a Full auto fill", mine(lease({ purpose: "full_auto" }))],
    ["a submit", mine(lease({ purpose: "apply_submit" }))],
    ["a profile edit", mine(lease({ purpose: "profile_edit" }))],
    [
      "the person's own look",
      mine(lease({ purpose: "look", automation: false })),
    ],
    [
      "the person's own sign in",
      mine(lease({ purpose: "signin", automation: false })),
    ],
    ["a sweep lease without the assistant", mine(lease({ automation: false }))],
    [
      "another website's lease, started after the run",
      mine(lease({ started_at: AFTER_RUN })),
    ],
    ["a lease of another person", { ...FREE, holder: "other" as const }],
  ])("shows the line and no view for %s", async (_, browser) => {
    const opened = fakeScreens();
    fakeApi({ detail: run(), browser });
    renderApp(`/runs/${ID}`);
    expect(await screen.findByText(ELSEWHERE)).toBeInTheDocument();
    await panel();
    expect(screen.queryByText(STARTING)).toBeNull();
    expect(screen.queryByTestId("live-screen")).toBeNull();
    expect(opened).toHaveLength(0);
  });

  it("shows the line while another purpose starts too", async () => {
    const opened = fakeScreens();
    fakeApi({
      detail: run(),
      browser: mine(
        lease({ purpose: "look", automation: false, state: "starting" }),
      ),
    });
    renderApp(`/runs/${ID}`);
    expect(await screen.findByText(ELSEWHERE)).toBeInTheDocument();
    expect(opened).toHaveLength(0);
  });
});

describe("another agent's run", () => {
  it.each(["queued", "running", "done", "failed"] as const)(
    "shows nothing of the browser when it is %s, and reads nothing",
    async (status: RunStatus) => {
      const opened = fakeScreens();
      const { calls } = fakeApi({
        detail: run({ agent: "job_assessor", status }),
        browser: mine(lease()),
      });
      renderApp(`/runs/${ID}`);
      await panel();
      expect(screen.queryByText(STARTING)).toBeNull();
      expect(screen.queryByText(ELSEWHERE)).toBeNull();
      expect(screen.queryByTestId("run-live-view")).toBeNull();
      expect(screen.queryByTestId("live-screen")).toBeNull();
      expect(opened).toHaveLength(0);
      expect(calls).toEqual([]);
    },
  );
});

describe("browserOfRun", () => {
  const AT = (iso: string) => lease({ started_at: iso });
  it.each([
    ["no read yet", undefined, "starting"],
    ["no lease", mine(null), "starting"],
    ["the lease is starting", mine(lease({ state: "starting" })), "starting"],
    ["active", mine(lease()), "live"],
    [
      "waiting for the person (Take over)",
      mine(lease({ state: "waiting_person", wait_reason: "takeover" })),
      "live",
    ],
    ["started with the run, to the millisecond", mine(AT(RUN_STARTED)), "live"],
    [
      "started a millisecond after the run",
      mine(AT("2026-10-29T09:00:03.001Z")),
      "elsewhere",
    ],
    [
      "started before the run, other zone notation",
      mine(AT("2026-10-29T10:00:00+01:00")),
      "live",
    ],
    ["a time that cannot be read", mine(AT("not a time")), "elsewhere"],
    ["no time", mine(AT("")), "elsewhere"],
    [
      "another person's holder",
      { ...FREE, holder: "other" as const },
      "elsewhere",
    ],
    [
      "a lease with the holder not set",
      { ...FREE, lease: lease(), holder: null },
      "starting",
    ],
    ["a purpose in other case", mine(lease({ purpose: "Sweep" })), "elsewhere"],
    ["a purpose with a tail", mine(lease({ purpose: "sweep2" })), "elsewhere"],
    ["no purpose", mine(lease({ purpose: "" })), "elsewhere"],
  ])("%s: %s", (_, browser, show) => {
    expect(browserOfRun(run(), browser).show).toBe(show);
  });

  it("fails closed when the run has no start time", () => {
    expect(browserOfRun(run({ started_at: null }), mine(lease())).show).toBe(
      "elsewhere",
    );
  });
});
