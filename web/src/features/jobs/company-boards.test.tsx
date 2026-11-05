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
import { sourceName } from "./labels";
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
  const dialog = await screen.findByRole("dialog", { name: "Find jobs" });
  return { profile, user, dialog, detected };
}

function savedBoards(profile: { saves: { body: { data: unknown } }[] }) {
  const last = profile.saves.at(-1)?.body.data as Search | undefined;
  return last?.company_boards;
}

const STARTER_SAVED = STARTER.map(({ company, ats, board, enabled }) => ({
  company,
  ats,
  board,
  enabled,
}));

function lastSearch(profile: { saves: { body: { data: unknown } }[] }) {
  return profile.saves.at(-1)?.body.data as Search;
}

async function save(
  user: ReturnType<typeof userEvent.setup>,
  dialog: HTMLElement,
  profile: { saves: unknown[] },
) {
  await user.click(
    within(dialog).getByRole("button", { name: "Save and start search" }),
  );
  await waitFor(() => {
    expect(profile.saves).toHaveLength(1);
  });
}

it("keeps no board list on first use: Save leaves the key out", async () => {
  const { profile, user, dialog } = await open(null);
  expect(
    within(dialog).getByText(/Nothing yet\. Add an address above/),
  ).toBeVisible();
  // The popular boards are ideas, folded away, never in the list by themselves.
  expect(
    within(dialog).queryByRole("button", { name: /Add Example Robotics/ }),
  ).not.toBeInTheDocument();
  await save(user, dialog, profile);
  const sent = profile.saves[0]?.body.data as Record<string, unknown>;
  expect(sent).not.toHaveProperty("company_boards");
});

it("adds every popular board with one click and saves them", async () => {
  const { profile, user, dialog } = await open(null);
  await user.click(
    within(dialog).getByRole("button", { name: "More company job boards" }),
  );
  await user.click(
    await within(dialog).findByRole("button", { name: "Add all 2" }),
  );
  const list = within(dialog).getByRole("list", { name: "Your list" });
  expect(within(list).getByText("Example Robotics")).toBeVisible();
  expect(
    within(list).getAllByText("Company job board on Greenhouse, read directly"),
  ).toHaveLength(1);
  await save(user, dialog, profile);
  expect(savedBoards(profile)).toEqual(STARTER_SAVED);
});

it("adds one popular board, never the rest", async () => {
  const { profile, user, dialog } = await open(null);
  await user.click(
    within(dialog).getByRole("button", { name: "More company job boards" }),
  );
  await user.click(
    await within(dialog).findByRole("button", {
      name: "Add Example Labs (Greenhouse)",
    }),
  );
  await save(user, dialog, profile);
  expect(savedBoards(profile)).toEqual([STARTER_SAVED[1]]);
});

it("saves a list saved before as it is", async () => {
  const { profile, user, dialog } = await open([]);
  await save(user, dialog, profile);
  // A list saved before (here the empty one) is the person's own: it is saved as it is.
  expect(savedBoards(profile)).toEqual([]);
});

it("reads a pasted board through its api, keeps a careers page as a website, and removes lines", async () => {
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
  const list = within(dialog).getByRole("list", { name: "Your list" });
  const address = within(dialog).getByLabelText("Website address");
  // Not a board: it becomes a website of her own, browsed by the assistant.
  await user.type(address, "example.org/careers");
  await user.click(within(dialog).getByRole("button", { name: "Add" }));
  expect(await within(list).findByText("example.org/careers")).toBeVisible();
  expect(
    within(list).getByText("Website of your choice, browsed by the assistant"),
  ).toBeVisible();
  expect(address).toHaveValue("");
  await user.type(address, "https://jobs.lever.co/exampleworks/abc");
  await user.click(within(dialog).getByRole("button", { name: "Add" }));
  expect(await within(list).findByText("Exampleworks")).toBeVisible();
  expect(
    within(list).getByText("Company job board on Lever, read directly"),
  ).toBeVisible();
  expect(detected).toEqual([
    "https://example.org/careers",
    "https://jobs.lever.co/exampleworks/abc",
  ]);
  // The same board again is refused.
  await user.type(address, "https://jobs.lever.co/exampleworks");
  await user.click(within(dialog).getByRole("button", { name: "Add" }));
  expect(await within(dialog).findByRole("alert")).toHaveTextContent(
    "Exampleworks is already in your list.",
  );
  await user.clear(address);
  await user.click(
    within(list).getByRole("button", { name: "Remove Example Robotics" }),
  );
  await save(user, dialog, profile);
  expect(savedBoards(profile)).toEqual([
    {
      company: "Example Labs",
      ats: "greenhouse",
      board: "examplelabs",
      enabled: true,
    },
    {
      company: "Exampleworks",
      ats: "lever",
      board: "exampleworks",
      enabled: true,
    },
  ]);
  expect(lastSearch(profile).interactive_sites).toEqual([
    {
      platform: "custom",
      url: "https://example.org/careers",
      mode: "llm_browser",
      enabled: true,
    },
  ]);
  // Much typing through user-event: about 4 to 5 s alone on the dev machine (4.7 s before
  // chapter 2 too), past the 5 s default when the whole suite runs in parallel.
}, 15_000);

it("refuses what is not an address before asking the api", async () => {
  const { user, dialog, detected } = await open([]);
  const address = within(dialog).getByLabelText("Website address");
  await user.type(address, "not an address");
  await user.click(within(dialog).getByRole("button", { name: "Add" }));
  expect(await within(dialog).findByRole("alert")).toHaveTextContent(
    "An address has no spaces.",
  );
  await user.clear(address);
  await user.type(address, "localhost");
  await user.click(within(dialog).getByRole("button", { name: "Add" }));
  expect(await within(dialog).findByRole("alert")).toHaveTextContent(
    "This is not a website address.",
  );
  expect(address).toHaveAttribute("aria-invalid", "true");
  expect(detected).toEqual([]);
});

it("shows a paused board and searches it again on request", async () => {
  const { profile, user, dialog } = await open([
    {
      company: "Example Labs",
      ats: "greenhouse",
      board: "examplelabs",
      enabled: false,
    },
  ]);
  const list = within(dialog).getByRole("list", { name: "Your list" });
  expect(within(list).getByText("Paused")).toBeVisible();
  await user.click(
    within(list).getByRole("button", { name: "Search it again" }),
  );
  expect(within(list).queryByText("Paused")).not.toBeInTheDocument();
  await save(user, dialog, profile);
  expect(savedBoards(profile)).toEqual([
    {
      company: "Example Labs",
      ats: "greenhouse",
      board: "examplelabs",
      enabled: true,
    },
  ]);
});

it("suggests boards from the person's postings, never adding them by itself", async () => {
  const { profile, user, dialog } = await open(
    [],
    [{ ...STARTER[0], postings: 3 }],
  );
  await user.click(
    within(dialog).getByRole("button", { name: "More company job boards" }),
  );
  const suggested = await within(dialog).findByRole("list", {
    name: "From your postings",
  });
  expect(
    within(dialog).getByText(/Nothing yet\. Add an address above/),
  ).toBeVisible();
  await user.click(
    within(suggested).getByRole("button", {
      name: "Add Example Robotics (Ashby)",
    }),
  );
  expect(
    within(dialog).queryByRole("list", { name: "From your postings" }),
  ).not.toBeInTheDocument();
  await save(user, dialog, profile);
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
  // The live line of a running sweep names a board by its company, else by its ATS and id.
  expect(
    sourceName({
      kind: "board",
      query: "greenhouse:examplelabs",
      company: "Example Labs",
    }),
  ).toBe("Company board, Example Labs");
  expect(sourceName({ kind: "board", query: "greenhouse:examplelabs" })).toBe(
    "Company board, greenhouse:examplelabs",
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
        board("ashby:example-robotics", {
          found: 3,
          new: 2,
          company: "Example Robotics",
        }),
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
    // Named by the company frozen with the sweep, else by its ATS and id (an older sweep).
    "Example Robotics: 3 postings matched, 2 new.",
    "ashby:example-two: 0 postings matched, 0 new. The ATS asked the app to slow down, so its other boards wait for the next search.",
    "ashby:example-three: Its ATS asked the app to slow down earlier in this search, so this board waits for the next one.",
  ]);
});
