import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { toast } from "sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FAILED_RUN, SWEEP_RUN } from "@/design-system/fixtures/activity";
import { fakeSession, member } from "@/test/auth";
import { phoneScreen } from "@/test/media";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import {
  activityCountKey,
  type ActivityCount,
  type WorkflowRunDetail,
} from "../api/activity-api";

const OLD = { id: "old-run", finished_at: "2026-11-10T08:00:00Z" };
const SEARCH: WorkflowRunDetail = {
  ...SWEEP_RUN,
  id: "search-run",
  workflow_title: "Job search",
  status: "success",
  subject: { words: "Robotics engineer in Lyon", job_id: null },
  new_postings: 12,
};
const ENDED_SEARCH = { id: SEARCH.id, finished_at: "2026-11-10T09:00:00Z" };
const FAILED: WorkflowRunDetail = {
  ...FAILED_RUN,
  id: "failed-run",
  workflow_title: "Application",
  subject: { words: "Platform engineer at Example Robotics", job_id: "j1" },
  reason: "Answer writer: The run reached its limit of 40 turns.",
};

/** Answers the count with each answer in turn, the last one again; serves the run summaries. */
function fakeActivity(answers: ActivityCount[], runs: WorkflowRunDetail[]) {
  const calls = { count: 0, summaries: [] as string[] };
  server.use(
    http.get("*/api/activity/count", () => {
      const answer = answers[Math.min(calls.count, answers.length - 1)];
      calls.count += 1;
      return HttpResponse.json(answer);
    }),
    http.get("*/api/activity/runs/:id", ({ params }) => {
      const id = String(params.id);
      calls.summaries.push(id);
      const found = runs.find((one) => one.id === id);
      return found
        ? HttpResponse.json(found)
        : HttpResponse.json({ detail: "Run not found." }, { status: 404 });
    }),
  );
  return calls;
}

const toasts = () => document.querySelectorAll("[data-sonner-toast]");

// A run that ends after the one a test expects no toast for: its toast shows that the count
// was read and acted on, so the absence of the other is not a read that had not landed yet.
const CONTROL: WorkflowRunDetail = {
  ...SEARCH,
  id: "control-run",
  workflow_title: "Job chat",
  subject: null,
  new_postings: null,
};
const ENDED_CONTROL = { id: CONTROL.id, finished_at: "2026-11-10T10:00:00Z" };

async function onlyTheControl(calls: { summaries: string[] }) {
  expect(await screen.findByText("Job chat: Succeeded")).toBeVisible();
  expect(toasts()).toHaveLength(1);
  expect(calls.summaries).toEqual([CONTROL.id]);
}

async function openAt(path: string) {
  const app = renderApp(path);
  await screen.findByRole("navigation", { name: "Main" });
  return app;
}

/** The shell's next read of the count, as its interval would make it. */
async function readCount(app: Awaited<ReturnType<typeof openAt>>) {
  await act(() => app.client.refetchQueries({ queryKey: activityCountKey }));
}

/** The shell's first count, read: what had ended when the page opened. */
async function firstCountRead(app: Awaited<ReturnType<typeof openAt>>) {
  await vi.waitFor(() => {
    expect(app.client.getQueryData(activityCountKey)).toBeDefined();
  });
  // The shell renders with it before the next read lands.
  await act(async () => {});
}

beforeEach(() => {
  window.localStorage.clear();
  fakeSession(member);
});

afterEach(() => {
  toast.dismiss();
});

describe("the toast of a run's end", () => {
  it("shows one toast with the search's words, its count of new postings and both links", async () => {
    const calls = fakeActivity(
      [
        { under_way: 1, ended: [OLD] },
        { under_way: 0, ended: [ENDED_SEARCH, OLD] },
      ],
      [SEARCH],
    );
    const app = await openAt("/jobs");
    await firstCountRead(app);
    expect(toasts()).toHaveLength(0);

    await readCount(app);
    const title = await screen.findByText("Job search: Succeeded");
    const shown = title.closest<HTMLElement>("[data-sonner-toast]");
    expect(shown).not.toBeNull();
    const card = within(shown as HTMLElement);
    expect(card.getByText("Robotics engineer in Lyon.")).toBeInTheDocument();
    expect(card.getByText("12 new postings added.")).toBeInTheDocument();
    expect(card.getByRole("link", { name: "Open the run" })).toHaveAttribute(
      "href",
      "/activity/runs/search-run",
    );
    expect(
      card.getByRole("link", { name: "See the new postings" }),
    ).toHaveAttribute("href", "/jobs?status=found");
    // In the toaster's polite live region: a screen reader says it when it is free.
    expect(shown?.closest("section")).toHaveAttribute("aria-live", "polite");
    expect(shown?.closest("[aria-live=assertive]")).toBeNull();

    await readCount(app);
    await readCount(app);
    expect(toasts()).toHaveLength(1);
    expect(calls.summaries).toEqual(["search-run"]);
  });

  it("says why a run failed, with no postings link", async () => {
    const ended = { id: FAILED.id, finished_at: "2026-11-10T09:30:00Z" };
    fakeActivity(
      [
        { under_way: 1, ended: [] },
        { under_way: 0, ended: [ended] },
      ],
      [FAILED],
    );
    const app = await openAt("/applications");
    await firstCountRead(app);
    await readCount(app);
    const title = await screen.findByText("Application: Failed");
    const card = within(
      title.closest<HTMLElement>("[data-sonner-toast]") as HTMLElement,
    );
    expect(
      card.getByText("Answer writer: The run reached its limit of 40 turns."),
    ).toBeInTheDocument();
    expect(
      card.getByText("Platform engineer at Example Robotics."),
    ).toBeInTheDocument();
    expect(
      card.queryByRole("link", { name: "See the new postings" }),
    ).not.toBeInTheDocument();
    expect(card.getByRole("link", { name: "Open the run" })).toHaveAttribute(
      "href",
      "/activity/runs/failed-run",
    );
  });

  it("shows none for a run that had ended when the page opened", async () => {
    const calls = fakeActivity(
      [
        { under_way: 1, ended: [ENDED_SEARCH, OLD] },
        { under_way: 1, ended: [ENDED_SEARCH, OLD] },
        { under_way: 0, ended: [ENDED_CONTROL, ENDED_SEARCH, OLD] },
      ],
      [SEARCH, CONTROL],
    );
    const app = await openAt("/jobs");
    await firstCountRead(app);
    await readCount(app);
    await readCount(app);
    await onlyTheControl(calls);
  });

  it("shows none for an older run that comes back into the list", async () => {
    const older = { id: "older-run", finished_at: "2026-11-10T07:00:00Z" };
    const calls = fakeActivity(
      [
        { under_way: 1, ended: [OLD] },
        { under_way: 0, ended: [ENDED_CONTROL, older] },
      ],
      [{ ...SEARCH, id: older.id }, CONTROL],
    );
    const app = await openAt("/jobs");
    await firstCountRead(app);
    await readCount(app);
    await onlyTheControl(calls);
  });

  it("does not show a run's toast again after a reload", async () => {
    const ended = { under_way: 1, ended: [ENDED_SEARCH, OLD] };
    fakeActivity([{ under_way: 2, ended: [OLD] }, ended], [SEARCH]);
    const first = await openAt("/jobs");
    await firstCountRead(first);
    await readCount(first);
    expect(await screen.findByText("Job search: Succeeded")).toBeVisible();
    first.unmount();
    act(() => {
      toast.dismiss();
    });

    const again = fakeActivity(
      [ended, { under_way: 0, ended: [ENDED_CONTROL, ENDED_SEARCH, OLD] }],
      [SEARCH, CONTROL],
    );
    const reloaded = await openAt("/jobs");
    await firstCountRead(reloaded);
    await readCount(reloaded);
    await onlyTheControl(again);
  });

  it("does not show a run another tab of this browser announced", async () => {
    window.localStorage.setItem(
      "activity:announced-runs",
      JSON.stringify([SEARCH.id]),
    );
    const calls = fakeActivity(
      [
        { under_way: 2, ended: [OLD] },
        { under_way: 1, ended: [ENDED_SEARCH, OLD] },
        { under_way: 0, ended: [ENDED_CONTROL, ENDED_SEARCH, OLD] },
      ],
      [SEARCH, CONTROL],
    );
    const app = await openAt("/jobs");
    await firstCountRead(app);
    await readCount(app);
    await readCount(app);
    await onlyTheControl(calls);
  });

  it("reads a summary that failed again at the next count", async () => {
    const calls = fakeActivity(
      [
        { under_way: 1, ended: [] },
        { under_way: 0, ended: [ENDED_SEARCH] },
      ],
      [],
    );
    const app = await openAt("/jobs");
    await firstCountRead(app);
    await readCount(app);
    await vi.waitFor(() => {
      expect(calls.summaries).toEqual(["search-run"]);
    });
    expect(toasts()).toHaveLength(0);

    fakeActivity([{ under_way: 0, ended: [ENDED_SEARCH] }], [SEARCH]);
    await readCount(app);
    expect(await screen.findByText("Job search: Succeeded")).toBeVisible();
  });

  it("follows the person from page to page, and shows on a phone", async () => {
    phoneScreen();
    fakeActivity(
      [
        { under_way: 1, ended: [] },
        { under_way: 0, ended: [ENDED_SEARCH] },
      ],
      [SEARCH],
    );
    const user = userEvent.setup();
    const app = await openAt("/jobs");
    await firstCountRead(app);
    await user.click(
      within(screen.getByRole("navigation", { name: "Main" })).getByRole(
        "link",
        { name: /^Profile/ },
      ),
    );
    expect(app.location()).toBe("/profile");
    await readCount(app);
    const title = await screen.findByText("Job search: Succeeded");
    await user.click(
      within(
        title.closest<HTMLElement>("[data-sonner-toast]") as HTMLElement,
      ).getByRole("link", { name: "See the new postings" }),
    );
    expect(app.location()).toBe("/jobs?status=found");
  });
});
