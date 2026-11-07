import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SweepProgress } from "@/components/shared/jobs/find-jobs";
import type { Browser, Lease } from "@/features/browser/api/browser-api";
import type { Sweep, SweepSource } from "@/features/jobs/api/sweeps-api";
import type { SweepRunner } from "@/features/jobs/api/use-sweep-runner";
import { FREE, connected, fakeScreens } from "@/test/browser";
import { renderWithQuery } from "@/test/render";
import { server } from "@/test/server";

afterEach(() => {
  vi.restoreAllMocks();
});

const VIEW = "Live view of your browser: careers.example.org";

/** A job website of the sweep, searched in the person's browser (api interactive.py). */
const website = (host: string, phase: string): SweepSource => ({
  source_id: `id-${host}`,
  kind: "custom",
  query: "",
  location: null,
  mode: "llm_browser",
  host,
  phase,
  result_pages: 0,
  found: 0,
  new: 0,
  skipped_by_rule: 0,
  items_skipped: 0,
  error: null,
});

function sweepOf(sources: SweepSource[]): Sweep {
  return {
    id: "11111111-2222-4333-8444-555555555555",
    trigger: "manual",
    status: "running",
    started_at: "2026-10-09T08:00:00Z",
    finished_at: null,
    error: null,
    boards_total: 0,
    boards_done: 0,
    boards_ok: 0,
    boards_failed: [],
    current_board: null,
    seen: 0,
    matched: 0,
    new: 0,
    duplicates: 0,
    skipped_by_rule: 0,
    duration: null,
    sources,
  };
}

function sweepLease(overrides: Partial<Lease> = {}): Lease {
  return {
    id: "5b2d0c7e-8f61-4a3b-9c4d-1e2f3a4b5c6d",
    purpose: "sweep",
    automation: true,
    state: "active",
    wait_reason: null,
    wait_host: null,
    wait_deadline: null,
    started_at: "2026-10-09T08:00:05Z",
    active_s: 30,
    max_s: 86_400,
    ...overrides,
  };
}

/** GET /api/browser as the api answers it; counts the reads. */
function browser(answer: Partial<Browser>) {
  const reads = { count: 0 };
  server.use(
    http.get("*/api/browser", () => {
      reads.count += 1;
      return HttpResponse.json({ ...FREE, ...answer });
    }),
  );
  return reads;
}

/** Find jobs' progress of a sweep reading `sources`, its browser opened with Watch the browser. */
async function progress(sources: SweepSource[], watch = true) {
  const runner = { running: sweepOf(sources) } as unknown as SweepRunner;
  renderWithQuery(<SweepProgress runner={runner} />);
  const user = userEvent.setup();
  if (watch)
    await user.click(
      await screen.findByRole("button", { name: "Watch the browser" }),
    );
  return user;
}

describe("the browser on Find jobs' progress", () => {
  it("shows the website searched in the person's browser, named, watched until Use the browser myself", async () => {
    const opened = fakeScreens();
    browser({ holder: "you", lease: sweepLease() });
    await progress([
      website("jobs.example.org", "done"),
      website("careers.example.org", "running"),
      website("jobs.example.net", "pending"),
    ]);
    const shown = await connected(opened);
    expect(shown.screen.viewOnly).toBe(true);
    const views = screen.getByRole("list", {
      name: "Websites searched in your browser",
    });
    const view = within(views).getByRole("region", { name: VIEW });
    await waitFor(() => {
      expect(within(view).getByText("Live")).toBeInTheDocument();
    });
    expect(
      within(view).getByRole("button", { name: "Use the browser myself" }),
    ).toBeEnabled();
    expect(within(views).getAllByRole("listitem")).toHaveLength(1);
  });

  it("says what the assistant asks, and that the view needs the person", async () => {
    const opened = fakeScreens();
    browser({
      holder: "you",
      lease: sweepLease({
        state: "waiting_person",
        wait_reason: "ask_person",
        wait_host: "careers.example.org",
      }),
    });
    await progress([website("careers.example.org", "running")]);
    await connected(opened);
    const view = screen.getByRole("region", { name: VIEW });
    expect(await within(view).findByText("Needs you")).toBeInTheDocument();
    expect(within(view).getByRole("status")).toHaveTextContent(
      "The assistant needs your help on careers.example.org.",
    );
  });

  it.each([
    [
      "the browser busy with other work",
      { holder: "other" as const },
      "The browser is busy. This website's search starts when it is free.",
    ],
    [
      "its place in the browser's line",
      {
        queue: [{ purpose: "sweep", position: 2, estimated_wait_s: 150 }],
      },
      "Your browser starts in about 3 minutes.",
    ],
    [
      "a lease still starting",
      { holder: "you" as const, lease: sweepLease({ state: "starting" }) },
      "Your browser is starting.",
    ],
    [
      "the person's own session",
      {
        holder: "you" as const,
        lease: sweepLease({ purpose: "signin", automation: false }),
      },
      "The browser is busy. This website's search starts when it is free.",
    ],
    [
      "a browser that does not answer",
      { available: false },
      "The browser is not available right now.",
    ],
  ])("says why there is no screen yet: %s", async (_, answer, sentence) => {
    const opened = fakeScreens();
    browser(answer);
    await progress([website("careers.example.org", "running")]);
    const view = await screen.findByRole("region", { name: VIEW });
    await waitFor(() => {
      expect(within(view).getByRole("status")).toHaveTextContent(sentence);
    });
    expect(within(view).queryByTestId("live-screen")).toBeNull();
    expect(opened).toHaveLength(0);
  });

  it("shows no view, and does not read the browser, while only the sources without a browser run", async () => {
    const reads = browser({ holder: "you", lease: sweepLease() });
    await progress(
      [
        {
          ...website("www.arbeitnow.com", "running"),
          kind: "arbeitnow",
          mode: "api",
        },
        website("careers.example.org", "pending"),
      ],
      false,
    );
    await screen.findByRole("region", { name: "Search in progress" });
    expect(
      screen.queryByRole("list", { name: "Websites searched in your browser" }),
    ).toBeNull();
    expect(screen.queryByRole("button", { name: /the browser$/ })).toBeNull();
    expect(reads.count).toBe(0);
  });

  it("opens no view, and reads no browser, until Watch the browser", async () => {
    // browserd takes an open view for a person who watches: an ask of the assistant then
    // waits for them, so the view opens only when the person asks for it.
    const opened = fakeScreens();
    const reads = browser({ holder: "you", lease: sweepLease() });
    await progress([website("careers.example.org", "running")], false);
    expect(
      screen.getByRole("button", { name: "Watch the browser" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: VIEW })).toBeNull();
    expect(reads.count).toBe(0);
    expect(opened).toHaveLength(0);
  });

  it("closes the view on Hide the browser, and opens it again on Watch the browser", async () => {
    const opened = fakeScreens();
    browser({ holder: "you", lease: sweepLease() });
    const user = await progress([website("careers.example.org", "running")]);
    const first = await connected(opened);
    await user.click(screen.getByRole("button", { name: "Hide the browser" }));
    expect(screen.queryByRole("region", { name: VIEW })).toBeNull();
    expect(first.screen.disconnected).toBe(true);
    await user.click(screen.getByRole("button", { name: "Watch the browser" }));
    await screen.findByRole("region", { name: VIEW });
    await waitFor(() => {
      expect(opened).toHaveLength(2);
    });
  });

  it("closes the view while the page is in the background, and opens it again on its return", async () => {
    const opened = fakeScreens();
    browser({ holder: "you", lease: sweepLease() });
    await progress([website("careers.example.org", "running")]);
    const first = await connected(opened);
    const visibility = vi
      .spyOn(document, "visibilityState", "get")
      .mockReturnValue("hidden");
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(screen.queryByRole("region", { name: VIEW })).toBeNull();
    expect(first.screen.disconnected).toBe(true);
    visibility.mockReturnValue("visible");
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await screen.findByRole("region", { name: VIEW });
    await waitFor(() => {
      expect(opened).toHaveLength(2);
    });
  });

  it("shows two websites searched at once, each in its own window, and asks each ticket for its slot", async () => {
    const opened = fakeScreens();
    const slots: unknown[] = [];
    server.use(
      http.post("*/api/browser/live/ticket", async ({ request }) => {
        slots.push(((await request.json()) as { slot: number }).slot);
        return HttpResponse.json(
          {
            ticket: "t".repeat(43),
            url: `/vnc/websockify?ticket=${"t".repeat(43)}`,
            expires_in_s: 30,
          },
          { status: 201 },
        );
      }),
    );
    const leases = [
      sweepLease({ slot: 0, source_id: "id-careers.example.org" }),
      sweepLease({
        id: "8c1e2f3a-4b5c-4d6e-8f70-91a2b3c4d5e6",
        slot: 1,
        source_id: "id-jobs.example.net",
      }),
    ];
    browser({ holder: "you", lease: leases[0], slots: leases });
    await progress([
      website("careers.example.org", "running"),
      website("jobs.example.net", "running"),
      website("jobs.example.org", "pending"),
    ]);
    await waitFor(() => {
      expect(opened).toHaveLength(2);
    });
    expect([...slots].sort()).toEqual([0, 1]);
    for (const host of ["careers.example.org", "jobs.example.net"])
      expect(
        screen.getByRole("region", {
          name: `Live view of your browser: ${host}`,
        }),
      ).toBeInTheDocument();
  });

  it("never shows a website the window of another website", async () => {
    const opened = fakeScreens();
    const other = sweepLease({ slot: 1, source_id: "id-jobs.example.net" });
    browser({ holder: "you", lease: other, slots: [other] });
    await progress([
      website("careers.example.org", "running"),
      website("jobs.example.org", "running"),
    ]);
    const waiting = await screen.findAllByText(
      "The browser is busy. This website's search starts when it is free.",
    );
    expect(waiting).toHaveLength(2);
    expect(opened).toHaveLength(0);
  });
});
