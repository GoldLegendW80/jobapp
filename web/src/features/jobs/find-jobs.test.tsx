import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { admin, fakeSession } from "@/test/auth";
import { fakeJobsApi, makeJob } from "@/test/jobs";
import { renderApp } from "@/test/render";
import { fakeProfile } from "@/test/profile";
import { emptySweeps, server } from "@/test/server";

import type {
  ScheduleState,
  Sweep,
  SweepDetail,
  SweepSource,
} from "./sweeps-api";
import { sweepTiming } from "./sweeps-api";

const ID = "11111111-2222-4333-8444-555555555555";

function makeSweep(overrides: Partial<Sweep> = {}): Sweep {
  return {
    id: ID,
    trigger: "manual",
    status: "running",
    started_at: "2026-10-09T08:00:00Z",
    finished_at: null,
    error: null,
    boards_total: 3,
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
    sources: [],
    ...overrides,
  };
}

function finished(overrides: Partial<SweepDetail> = {}): SweepDetail {
  return {
    ...makeSweep({
      status: "done",
      finished_at: "2026-10-09T08:01:00Z",
      boards_done: 3,
      boards_ok: 2,
      seen: 120,
      matched: 4,
      new: 2,
      duplicates: 2,
      duration: 12.5,
      boards_failed: [
        {
          company: "Gone Corp",
          ats: "lever",
          org: "gone",
          error: "not_found",
          message: "board not found",
        },
      ],
    }),
    new_jobs: [
      {
        id: "aaaaaaaa-0000-4000-9000-000000000001",
        company: "Mistral AI",
        title: "Applied AI Engineer",
        location: "Paris",
        fit: 82,
        status: "found",
      },
      {
        id: "aaaaaaaa-0000-4000-9000-000000000002",
        company: "Anthropic",
        title: "Forward Deployed Engineer",
        location: "London",
        fit: null,
        status: "found",
      },
    ],
    ...overrides,
  };
}

/**
 * A fake sweeps api: POST answers `start` (a Response or the sweep), GET /{id} walks through
 * `states` one per call (the last repeats), GET list answers `list`.
 */
function fakeSweeps(options: {
  start?: () => Response;
  states?: Array<Sweep | SweepDetail | Response>;
  list?: Sweep[];
  schedule?: Partial<ScheduleState>;
}) {
  const calls = { start: 0, detail: 0, list: 0 };
  const states = options.states ?? [];
  server.use(
    http.post("*/api/jobs/sweeps", () => {
      calls.start += 1;
      return options.start
        ? options.start()
        : HttpResponse.json(makeSweep(), { status: 202 });
    }),
    http.get("*/api/jobs/sweeps", () => {
      calls.list += 1;
      return HttpResponse.json({
        items: options.list ?? [],
        schedule: { ...emptySweeps.schedule, ...options.schedule },
      });
    }),
    http.get("*/api/jobs/sweeps/:id", () => {
      const state = states[Math.min(calls.detail, states.length - 1)];
      calls.detail += 1;
      if (state instanceof Response) return state;
      return HttpResponse.json(
        "new_jobs" in (state ?? {}) ? state : { ...state, new_jobs: [] },
      );
    }),
  );
  return calls;
}

beforeEach(() => {
  fakeSession(admin);
  fakeProfile();
  sweepTiming.pollMs = 20;
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
});
afterEach(() => {
  sweepTiming.pollMs = 1500;
});

async function openJobs() {
  const jobs = fakeJobsApi([makeJob(1)]);
  const app = renderApp("/jobs");
  await screen.findByRole("table", { name: "Postings" });
  return { jobs, app };
}

async function startSearch(user = userEvent.setup()) {
  await user.click(screen.getByRole("button", { name: "Find jobs" }));
  const setup = await screen.findByRole("dialog", { name: "Find jobs setup" });
  await user.click(
    within(setup).getByRole("button", { name: "Save and Start" }),
  );
}

describe("Find jobs", () => {
  it("starts a sweep, shows live progress, then the summary, and refreshes the list", async () => {
    // Slow enough that the progress line is seen before the end, even on a loaded machine.
    sweepTiming.pollMs = 300;
    const calls = fakeSweeps({
      states: [
        makeSweep({ boards_done: 1, current_board: "Mistral AI", new: 1 }),
        makeSweep({ boards_done: 2, current_board: "Anthropic", new: 2 }),
        finished(),
      ],
    });
    const { jobs } = await openJobs();
    const before = jobs.requests.length;
    const user = userEvent.setup();
    await startSearch(user);

    const progress = await screen.findByRole("region", {
      name: "Sweep in progress",
    });
    await waitFor(() => {
      expect(progress).toHaveTextContent(/Reading (Mistral AI|Anthropic)/);
    });
    expect(within(progress).getByRole("progressbar")).toHaveAttribute(
      "aria-valuemax",
      "3",
    );
    expect(screen.getByRole("button", { name: "Finding jobs" })).toBeDisabled();

    const dialog = await screen.findByRole("dialog", {
      name: "2 new postings found",
    });
    expect(dialog).toHaveTextContent(
      "2 boards read, 120 postings seen, 4 matched your search, 2 new postings.",
    );
    expect(dialog).toHaveTextContent("2 matches already in your list.");
    const links = within(
      within(dialog).getByRole("region", { name: "New postings" }),
    ).getAllByRole("link");
    expect(links.map((l) => l.getAttribute("href"))).toEqual([
      "/jobs/aaaaaaaa-0000-4000-9000-000000000001",
      "/jobs/aaaaaaaa-0000-4000-9000-000000000002",
    ]);
    expect(links[0]).toHaveTextContent("Applied AI Engineer");
    expect(within(links[0]!).getByLabelText("Fit 82 out of 100")).toBeTruthy();
    expect(within(links[1]!).getByLabelText("Unscored")).toBeTruthy();
    const failed = within(dialog).getByRole("region", {
      name: "Failed boards",
    });
    expect(failed).toHaveTextContent("1 board could not be read");
    expect(failed).toHaveTextContent(
      "Gone Corp (lever, gone): board not found",
    );

    expect(calls.start).toBe(1);
    await waitFor(() => {
      expect(jobs.requests.length).toBeGreaterThan(before);
    });
    expect(
      screen.queryByRole("region", { name: "Sweep in progress" }),
    ).not.toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Done" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Find jobs" })).toBeEnabled();
  });

  it("opens a new posting from the summary", async () => {
    fakeSweeps({ states: [finished()] });
    server.use(
      http.get("*/api/jobs/:id", () =>
        HttpResponse.json({ detail: "Job not found." }, { status: 404 }),
      ),
    );
    const { app } = await openJobs();
    const user = userEvent.setup();
    await startSearch(user);
    const dialog = await screen.findByRole("dialog", {
      name: "2 new postings found",
    });
    await user.click(within(dialog).getByRole("link", { name: /Applied AI/ }));
    expect(app.location()).toBe("/jobs/aaaaaaaa-0000-4000-9000-000000000001");
  });

  it("shows each job site read in the browser in plain words", async () => {
    const source = (
      kind: string,
      query: string,
      found: number,
      fresh: number,
      error: string | null,
      location: string | null = null,
    ) => ({
      kind,
      query,
      location,
      result_pages: 1,
      found,
      new: fresh,
      skipped_by_rule: 0,
      items_skipped: 0,
      error,
    });
    fakeSweeps({
      // The running state for several polls, so the progress is still there when it is read on
      // a loaded machine.
      states: [
        ...Array.from({ length: 10 }, () =>
          makeSweep({
            boards_total: 0,
            current_board: "google_careers:ai engineer/Paris",
          }),
        ),
        finished({
          sources: [
            source("google_careers", "ai engineer", 3, 2, null, "Paris"),
            source("google_careers", "agents", 0, 0, "layout_changed"),
            source("vie", "data", 0, 0, "browser_busy"),
            source("vie", "ml", 1, 1, "lease_ended"),
            { ...source("vie", "cv", 0, 0, "not_started"), result_pages: 0 },
          ],
        }),
      ],
    });
    await openJobs();
    const user = userEvent.setup();
    await startSearch(user);
    const progress = await screen.findByRole("region", {
      name: "Sweep in progress",
    });
    expect(progress).toHaveTextContent("Reading job sites in your browser");
    expect(progress).toHaveTextContent(
      "Reading Google Careers, ai engineer, Paris",
    );
    const dialog = await screen.findByRole("dialog", {
      name: "2 new postings found",
    });
    const sites = within(dialog).getByRole("region", {
      name: "Job sites in your browser",
    });
    const lines = within(sites)
      .getAllByRole("listitem")
      .map((li) => li.textContent);
    expect(lines).toEqual([
      "Google Careers, ai engineer, Paris: 3 postings found, 2 new.",
      "Google Careers, agents: 0 postings found, 0 new. The page looked different from what the app knows, so nothing was kept from it.",
      "Business France VIE, data: 0 postings found, 0 new. Your browser was in use, so this site waits for the next search.",
      "Business France VIE, ml: 1 posting found, 1 new. You took your browser back, so this site stopped. What was read is kept, the rest comes at the next search.",
      "Business France VIE, cv: not read this time, it comes at the next search.",
    ]);
  });

  it("separates API sources and explains interactive sites that were not searched", async () => {
    const source = (kind: string, overrides: Partial<SweepSource> = {}) => ({
      kind,
      query: "ai",
      location: null,
      result_pages: 0,
      found: 0,
      new: 0,
      skipped_by_rule: 0,
      items_skipped: 0,
      error: null,
      ...overrides,
    });
    fakeSweeps({
      states: [
        finished({
          sources: [
            source("api_board", { mode: "api", outcome: "complete", found: 3 }),
            source("api_failed", {
              mode: "api",
              outcome: "failed",
              error: "not_found",
            }),
            source("google_careers"),
            source("linkedin", {
              mode: "llm_browser",
              query: "pending",
              phase: "pending",
            }),
            source("linkedin", {
              mode: "llm_browser",
              query: "not attempted",
              phase: "done",
              outcome: "not_attempted",
            }),
            source("linkedin", {
              mode: "llm_browser",
              query: "waiting",
              phase: "done",
              reason: "interactive_executor_pending",
              error: "interactive_executor_pending",
            }),
            source("linkedin", {
              mode: "llm_browser",
              query: "searched",
              phase: "done",
              outcome: "no_matches",
            }),
            // One native search per title and place combination (owner, 2026-10-26).
            source("indeed", {
              mode: "llm_browser",
              query: "",
              phase: "done",
              outcome: "partial",
              found: 2,
              searches: 3,
              searches_planned: 3,
              searches_total: 3,
              error: "finding_persistence_pending",
            }),
            source("welcome_to_the_jungle", {
              mode: "llm_browser",
              query: "",
              phase: "done",
              outcome: "partial",
              found: 1,
              searches: 6,
              searches_planned: 6,
              searches_total: 9,
              error: "page_budget",
            }),
            source("remoteok", { mode: "feed", query: "", found: 2, new: 1 }),
          ],
        }),
      ],
    });
    await openJobs();
    await startSearch();
    const dialog = await screen.findByRole("dialog", {
      name: "2 new postings found",
    });
    const sites = within(dialog).getByRole("region", {
      name: "Job sites in your browser",
    });
    expect(
      within(sites)
        .getAllByRole("listitem")
        .map((li) => li.textContent),
    ).toEqual([
      "Google Careers, ai: 0 postings found, 0 new.",
      "LinkedIn, pending: not searched this time.",
      "LinkedIn, not attempted: not searched this time.",
      "LinkedIn, waiting: not searched this time.",
      "LinkedIn, searched: 0 postings found, 0 new.",
      "Indeed: 3 searches ran. 2 postings found, 0 new. Postings were read on this website, but saving them came with a later update.",
      "Welcome to the Jungle: 6 searches ran. At most 6 of your 9 title and place combinations are searched on a website, in the order you saved them. 1 posting found, 0 new. The search read as many pages as it may. What was read is kept.",
    ]);
    expect(dialog).not.toHaveTextContent(
      /api_board|api_failed|interactive_executor_pending|It stopped early/,
    );
    expect(dialog).toHaveTextContent("2 boards read, 120 postings seen");
    expect(
      within(dialog).getByRole("region", { name: "Failed boards" }),
    ).toHaveTextContent("Gone Corp (lever, gone): board not found");
    expect(
      within(dialog).getByRole("region", {
        name: "Freelance and project sources",
      }),
    ).toHaveTextContent("Remote OK: 2 postings found, 1 new.");
  });

  it("says it reads the freelance and project sources while it does (O8)", async () => {
    fakeSweeps({
      states: [makeSweep({ boards_total: 0, current_board: "Remote OK" })],
    });
    await openJobs();
    const user = userEvent.setup();
    await startSearch(user);
    const progress = await screen.findByRole("region", {
      name: "Sweep in progress",
    });
    await waitFor(() => {
      expect(progress).toHaveTextContent(
        "Reading freelance and project sources",
      );
    });
    expect(progress).toHaveTextContent("Reading Remote OK");
    expect(progress).not.toHaveTextContent("job sites in your browser");
  });

  it.each([0, 1, 2])(
    "names freelance feeds and %s parser skips apart, in plain words (O8)",
    async (skipped) => {
      const skipSentence =
        skipped === 0
          ? ""
          : skipped === 1
            ? " 1 feed item could not be read and was skipped."
            : " 2 feed items could not be read and were skipped.";
      const feed = (
        kind: string,
        found: number,
        fresh: number,
        ruled: number,
        error: string | null,
      ) => ({
        kind,
        query: "",
        location: null,
        result_pages: 0,
        found,
        new: fresh,
        skipped_by_rule: ruled,
        items_skipped: skipped,
        error,
      });
      fakeSweeps({
        states: [
          finished({
            sources: [
              {
                ...feed("google_careers", 1, 1, 0, null),
                query: "ai engineer",
                result_pages: 1,
              },
              feed("remoteok", 4, 2, 1, null),
              feed("weworkremotely", 0, 0, 0, "rate_limited"),
              feed("hackernews_freelance", 3, 0, 2, "time_budget"),
              feed("hackernews_hiring", 0, 0, 0, "stub_missing"),
            ],
          }),
        ],
      });
      await openJobs();
      const user = userEvent.setup();
      await startSearch(user);
      const dialog = await screen.findByRole("dialog", {
        name: "2 new postings found",
      });
      const sites = within(dialog).getByRole("region", {
        name: "Job sites in your browser",
      });
      expect(within(sites).getAllByRole("listitem")).toHaveLength(1);
      expect(sites).not.toHaveTextContent("Remote OK");
      expect(sites).not.toHaveTextContent("feed item");
      const feeds = within(dialog).getByRole("region", {
        name: "Freelance and project sources",
      });
      const lines = within(feeds)
        .getAllByRole("listitem")
        .map((li) => li.textContent);
      expect(lines).toEqual([
        "Remote OK: 4 postings found, 2 new. 1 posting was set aside by your roles to never apply for." +
          skipSentence,
        "We Work Remotely: 0 postings found, 0 new." +
          skipSentence +
          " The site asked the app to slow down, so it is read again at the next search.",
        "Hacker News, freelance thread: 3 postings found, 0 new. 2 postings were set aside by your roles to never apply for." +
          skipSentence +
          " The search ran out of time before this feed was read to the end. What was read is kept.",
        "Hacker News, hiring thread: 0 postings found, 0 new." +
          skipSentence +
          " This server is a test server that reads no real feed, so it was not read.",
      ]);
      for (const line of lines) expect(line).not.toMatch(/[‒–—―]|\s-\s/);
      expect(feeds).not.toHaveTextContent("It stopped early.");
    },
  );

  it("follows the sweep already running on a second start (409)", async () => {
    fakeSweeps({
      start: () =>
        HttpResponse.json(
          { detail: "A sweep is already running.", sweep_id: ID },
          { status: 409 },
        ),
      states: [
        makeSweep({ boards_done: 1 }),
        finished({ new: 0, new_jobs: [] }),
      ],
    });
    await openJobs();
    await startSearch();
    expect(
      await screen.findByText("A sweep is already running: following it."),
    ).toBeInTheDocument();
    expect(
      await screen.findByRole("dialog", { name: "No new posting" }),
    ).toBeInTheDocument();
  });

  it("explains a profile without boards (422) and offers to open it", async () => {
    fakeSweeps({
      start: () =>
        HttpResponse.json(
          {
            detail:
              "No board is enabled in the search section of your profile.",
          },
          { status: 422 },
        ),
    });
    const { app } = await openJobs();
    const user = userEvent.setup();
    await startSearch(user);
    expect(
      await screen.findByText(
        /No board is enabled in the search section of your profile/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Save and Start" }),
    ).toBeEnabled();
    await user.click(
      screen.getByRole("link", {
        name: "Manage API boards, feeds and search options",
      }),
    );
    expect(app.location()).toBe("/profile?tab=search");
  });

  it("asks to save the Search tab again when the stored section no longer passes (422)", async () => {
    const detail =
      "The search section of your profile no longer passes the checks. Open the Search tab of your profile and save it again.";
    fakeSweeps({
      start: () => HttpResponse.json({ detail }, { status: 422 }),
    });
    const { app } = await openJobs();
    const user = userEvent.setup();
    await startSearch(user);
    await screen.findByText(
      new RegExp("The search section of your profile no longer passes"),
    );
    expect(
      screen.getByRole("button", { name: "Save and Start" }),
    ).toBeEnabled();
    await user.click(
      screen.getByRole("link", {
        name: "Manage API boards, feeds and search options",
      }),
    );
    expect(app.location()).toBe("/profile?tab=search");
  });

  it("reports a sweep that could not start (503) and stays usable", async () => {
    fakeSweeps({
      start: () =>
        HttpResponse.json(
          { detail: "The sweep could not be queued. Try again in a minute." },
          { status: 503 },
        ),
    });
    await openJobs();
    await startSearch();
    expect(
      await screen.findByText(
        /The search did not start\. The API answered 503/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Save and Start" }),
    ).toBeEnabled();
  });

  it("follows a sweep already running when the page opens", async () => {
    const running = makeSweep({
      trigger: "schedule",
      boards_done: 2,
      current_board: "Datadog",
      new: 4,
    });
    fakeSweeps({ list: [running], states: [running, finished()] });
    await openJobs();
    const progress = await screen.findByRole("region", {
      name: "Sweep in progress",
    });
    expect(progress).toHaveTextContent(/2 of 3 boards read/);
    expect(
      await screen.findByRole("dialog", { name: "2 new postings found" }),
    ).toBeInTheDocument();
  });

  it("keeps polling through a failed request, and shows a failed sweep's error", async () => {
    fakeSweeps({
      states: [
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
        makeSweep({ boards_done: 1 }),
        finished({
          status: "failed",
          error: "The sweep took too long and was stopped.",
          new: 1,
          new_jobs: [finished().new_jobs[0]!],
          skipped_by_rule: 1,
        }),
      ],
    });
    await openJobs();
    await startSearch();
    const dialog = await screen.findByRole("dialog", {
      name: "The sweep stopped",
    });
    expect(dialog).toHaveTextContent(
      "The sweep took too long and was stopped.",
    );
    expect(dialog).toHaveTextContent(
      "1 new posting matched a never apply rule and was set to Skipped.",
    );
  });

  it("stops following a sweep that disappeared (404)", async () => {
    const calls = fakeSweeps({
      states: [
        HttpResponse.json({ detail: "Sweep not found." }, { status: 404 }),
      ],
    });
    await openJobs();
    await startSearch();
    await waitFor(() => {
      expect(
        screen.queryByRole("region", { name: "Sweep in progress" }),
      ).not.toBeInTheDocument();
    });
    const seen = calls.detail;
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(calls.detail).toBe(seen);
    expect(screen.getByRole("button", { name: "Find jobs" })).toBeEnabled();
  });
});

describe("Sweep history", () => {
  it("lists the last sweeps with the automatic schedule and opens one", async () => {
    fakeSweeps({
      schedule: {
        enabled: true,
        active: true,
        next_run: "2026-10-10T04:17:00Z",
      },
      list: [
        makeSweep({
          id: "b0000000-0000-4000-9000-000000000001",
          status: "done",
          trigger: "schedule",
          new: 3,
          started_at: "2026-10-09T04:17:00Z",
        }),
        makeSweep({
          id: "b0000000-0000-4000-9000-000000000002",
          status: "failed",
          new: 0,
          boards_failed: [
            {
              company: "X",
              ats: "ashby",
              org: "x",
              error: "timeout",
              message: "no answer in time",
            },
          ],
        }),
      ],
      states: [finished()],
    });
    await openJobs();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Sweep history" }));
    const dialog = await screen.findByRole("dialog", { name: "Sweeps" });
    expect(
      within(dialog).getByRole("region", { name: "Automatic search" }),
    ).toHaveTextContent(
      "Automatic search: on, every day at 06:17 (Europe/Paris). Next: Sat 10 Oct, 06:17 (Europe/Paris).",
    );
    const items = within(
      within(dialog).getByRole("list", { name: "Last sweeps" }),
    ).getAllByRole("button");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent(/Automatic, Done/);
    expect(items[0]).toHaveTextContent("3 new");
    expect(items[1]).toHaveTextContent(/By hand, Failed, 1 board failed/);
    await user.click(items[0]!);
    expect(
      await screen.findByRole("dialog", { name: "2 new postings found" }),
    ).toBeInTheDocument();
  });

  it("says when there is no sweep yet and the automatic search is off", async () => {
    fakeSweeps({ list: [] });
    await openJobs();
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Sweep history" }));
    const dialog = await screen.findByRole("dialog", { name: "Sweeps" });
    expect(dialog).toHaveTextContent("Automatic search: off.");
    expect(dialog).toHaveTextContent("No sweep yet.");
  });
});
