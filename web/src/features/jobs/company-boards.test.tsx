import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, expect, it, vi } from "vitest";

import { EMPTY, type CompanyBoard, type Search } from "@/features/profile/api";
import { admin, fakeSession } from "@/test/auth";
import { fakeJobsApi, makeJob } from "@/test/jobs";
import { fakeProfile } from "@/test/profile";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import { boardKey, boardPage } from "./boards-api";
import { SweepSources } from "./sweep-sources";
import type { SweepSource } from "./sweeps-api";

const STARTER = [
  {
    company: "Example Robotics",
    ats: "ashby",
    board: "example-robotics",
    enabled: true,
    url: "https://jobs.ashbyhq.com/example-robotics",
  },
  {
    company: "Example Labs",
    ats: "greenhouse",
    board: "examplelabs",
    enabled: true,
    url: "https://job-boards.greenhouse.io/examplelabs",
  },
];

beforeEach(() => {
  fakeSession(admin);
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
});

async function open(
  boards: CompanyBoard[] | null,
  suggestions: unknown[] = [],
) {
  const search: Search = { ...structuredClone(EMPTY.search) };
  if (boards !== null) search.company_boards = boards;
  const profile = fakeProfile({ search }, 6);
  fakeJobsApi([makeJob(1)]);
  const detected: string[] = [];
  server.use(
    http.get("*/api/browser/sites", () => HttpResponse.json({ sites: [] })),
    http.get("*/api/jobs/boards/starter", () =>
      HttpResponse.json({ checked_on: "2026-10-30", boards: STARTER }),
    ),
    http.get("*/api/jobs/boards/suggestions", () =>
      HttpResponse.json({ boards: suggestions }),
    ),
    http.post("*/api/jobs/boards/detect", async ({ request }) => {
      const { url } = (await request.json()) as { url: string };
      detected.push(url);
      if (!url.includes("jobs.lever.co"))
        return HttpResponse.json(
          {
            detail:
              "This address is not a company job board the app reads. Paste the board of an Ashby, Greenhouse, Lever, SmartRecruiters, Workable or Recruitee page, such as https://jobs.ashbyhq.com/example.",
          },
          { status: 422 },
        );
      return HttpResponse.json({
        company: "Exampleworks",
        ats: "lever",
        board: "exampleworks",
        enabled: true,
        url: "https://jobs.lever.co/exampleworks",
      });
    }),
    http.post("*/api/jobs/sweeps", () =>
      HttpResponse.json({ detail: "stop here" }, { status: 403 }),
    ),
  );
  renderApp("/jobs");
  await screen.findByRole("table", { name: "Postings" });
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Find jobs" }));
  const dialog = await screen.findByRole("dialog", { name: "Find jobs setup" });
  return { profile, user, dialog, detected };
}

function savedBoards(profile: { saves: { body: { data: unknown } }[] }) {
  const last = profile.saves.at(-1)?.body.data as Search | undefined;
  return last?.company_boards;
}

it("offers the starter list ticked on first use and saves it", async () => {
  const { profile, user, dialog } = await open(null);
  const list = await within(dialog).findByRole("list", {
    name: "Company job boards",
  });
  expect(
    within(list)
      .getAllByRole("checkbox")
      .map((box) => (box as HTMLInputElement).checked),
  ).toEqual([true, true]);
  expect(within(list).getByRole("link", { name: "Ashby" })).toHaveAttribute(
    "href",
    "https://jobs.ashbyhq.com/example-robotics",
  );
  await user.click(
    within(dialog).getByRole("button", { name: "Save and Start" }),
  );
  await waitFor(() => {
    expect(profile.saves).toHaveLength(1);
  });
  expect(savedBoards(profile)).toEqual(
    STARTER.map(({ company, ats, board, enabled }) => ({
      company,
      ats,
      board,
      enabled,
    })),
  );
});

it("adds a pasted board, pauses and removes boards", async () => {
  const mine: CompanyBoard[] = [
    {
      company: "Example Labs",
      ats: "greenhouse",
      board: "examplelabs",
      enabled: true,
    },
    {
      company: "Example Robotics",
      ats: "ashby",
      board: "example-robotics",
      enabled: true,
    },
  ];
  const { profile, user, dialog, detected } = await open(mine);
  const list = within(dialog).getByRole("list", { name: "Company job boards" });
  const address = within(dialog).getByLabelText("Board address");
  await user.type(address, "https://example.org/careers");
  await user.click(within(dialog).getByRole("button", { name: "Add board" }));
  expect(await within(dialog).findByRole("alert")).toHaveTextContent(
    "not a company job board",
  );
  await user.clear(address);
  await user.type(address, "https://jobs.lever.co/exampleworks/abc");
  await user.click(within(dialog).getByRole("button", { name: "Add board" }));
  await within(list).findByText("Exampleworks");
  expect(detected).toEqual([
    "https://example.org/careers",
    "https://jobs.lever.co/exampleworks/abc",
  ]);
  expect(address).toHaveValue("");
  // The same board again is refused without asking the api.
  await user.type(address, "https://jobs.lever.co/exampleworks");
  await user.click(within(dialog).getByRole("button", { name: "Add board" }));
  expect(await within(dialog).findByRole("alert")).toHaveTextContent(
    "Exampleworks is already in your list.",
  );
  await user.clear(address);
  await user.click(within(list).getAllByRole("checkbox")[0] as HTMLElement);
  await user.click(
    within(list).getByRole("button", { name: "Remove Example Robotics" }),
  );
  await user.click(
    within(dialog).getByRole("button", { name: "Save and Start" }),
  );
  await waitFor(() => {
    expect(profile.saves).toHaveLength(1);
  });
  expect(savedBoards(profile)).toEqual([
    {
      company: "Example Labs",
      ats: "greenhouse",
      board: "examplelabs",
      enabled: false,
    },
    {
      company: "Exampleworks",
      ats: "lever",
      board: "exampleworks",
      enabled: true,
    },
  ]);
});

it("suggests boards from the person's postings, never adding them by itself", async () => {
  const { profile, user, dialog } = await open(
    [],
    [{ ...STARTER[0], postings: 3 }],
  );
  const suggested = await within(dialog).findByRole("list", {
    name: "Suggested boards",
  });
  expect(
    within(dialog).getByText("No company board yet. Paste one below."),
  ).toBeVisible();
  await user.click(
    within(suggested).getByRole("button", {
      name: "Add Example Robotics (Ashby)",
    }),
  );
  expect(
    within(dialog).queryByRole("list", { name: "Suggested boards" }),
  ).not.toBeInTheDocument();
  await user.click(
    within(dialog).getByRole("button", { name: "Save and Start" }),
  );
  await waitFor(() => {
    expect(profile.saves).toHaveLength(1);
  });
  expect(savedBoards(profile)).toEqual([
    {
      company: "Example Robotics",
      ats: "ashby",
      board: "example-robotics",
      enabled: true,
    },
  ]);
});

it("names boards and their pages as the api does", () => {
  expect(boardKey({ ats: "smartrecruiters", board: "Example" })).toBe(
    "smartrecruiters:example",
  );
  expect(boardPage({ ats: "recruitee", board: "examplelabs" })).toBe(
    "https://examplelabs.recruitee.com/",
  );
  expect(boardPage({ ats: "workable", board: "examplemodels" })).toBe(
    "https://apply.workable.com/examplemodels/",
  );
});

it("lists each board of a sweep with what it kept or why it was not read", () => {
  const board = (query: string, more: Partial<SweepSource>): SweepSource => ({
    kind: "board",
    mode: "api",
    query,
    location: null,
    host: "api.ashbyhq.com",
    result_pages: 1,
    found: 0,
    new: 0,
    skipped_by_rule: 0,
    items_skipped: 0,
    error: null,
    ...more,
  });
  render(
    <SweepSources
      sources={[
        board("ashby:example-robotics", { found: 3, new: 2 }),
        board("ashby:example-two", { error: "rate_limited" }),
        board("ashby:example-three", {
          error: "not_started",
          reason: "host_rate_limited",
        }),
      ]}
    />,
  );
  const section = screen.getByRole("region", { name: "Company job boards" });
  const lines = within(section)
    .getAllByRole("listitem")
    .map((item) => item.textContent);
  expect(lines).toEqual([
    "ashby:example-robotics: 3 postings matched, 2 new.",
    "ashby:example-two: 0 postings matched, 0 new. The ATS asked the app to slow down, so its other boards wait for the next search.",
    "ashby:example-three: Its ATS asked the app to slow down earlier in this search, so this board waits for the next one.",
  ]);
});
