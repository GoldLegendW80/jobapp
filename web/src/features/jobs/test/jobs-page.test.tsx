import {
  act,
  cleanup,
  fireEvent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { admin, fakeSession } from "@/test/auth";
import {
  csvJobs,
  CSV_COUNTS,
  fakeJobsApi,
  makeDetail,
  makeJob,
} from "@/test/jobs";
import { phoneScreen } from "@/test/media";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import { PAGE_SIZE } from "../api/jobs-api";
import { SEARCH_DEBOUNCE_MS } from "../helpers/filters";
import { ITEM_ESTIMATE } from "../helpers/jobs-list";

const kpi = (label: string) =>
  screen.getByRole("button", { name: new RegExp(`^${label}: `) });
const list = () => screen.getByRole("list", { name: "Postings" });
const dataRows = () =>
  within(list())
    .getAllByRole("listitem")
    .filter((item) => item.hasAttribute("data-index"));
const pane = () => screen.getByRole("region", { name: "Selected posting" });
const lastRequest = (state: { requests: URLSearchParams[] }) =>
  state.requests.at(-1)!;
/** The id makeJob gives posting `n`. */
const idOf = (n: number) => makeJob(n).id;

/** Scroll the window (jsdom does not), so the window virtualiser sees it. */
function scrollWindowTo(y: number) {
  Object.defineProperty(window, "scrollY", { value: y, configurable: true });
  Object.defineProperty(window, "pageYOffset", {
    value: y,
    configurable: true,
  });
  fireEvent.scroll(window);
}

/** Scroll the list's own box (the pane beside the detail, from 1024px), as jsdom does not. */
function scrollListTo(y: number) {
  const box = list().parentElement!;
  Object.defineProperty(box, "scrollTop", { value: y, configurable: true });
  fireEvent.scroll(box);
}

/** GET /api/jobs/{id} for any posting makeJob made: the detail pane shows one from 1024px. */
function fakeDetails() {
  server.use(
    http.get("*/api/jobs/:id", ({ params }) => {
      const id = String(params.id);
      const n = /^00000000-0000-4000-9000-(\d{12})$/.exec(id)?.[1];
      return n ? HttpResponse.json(makeDetail(makeJob(Number(n)))) : undefined;
    }),
  );
}

/** The sheet of the toolbar's Filters button, opened. */
async function openFilters(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: /^Filters/ }));
  return screen.findByRole("dialog", { name: "Filters" });
}

beforeEach(() => {
  fakeSession(admin);
  fakeDetails();
  scrollWindowTo(0);
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
});

async function openJobs(path = "/jobs") {
  const app = renderApp(path);
  await screen.findByRole("heading", { level: 1, name: "Jobs" });
  await within(
    await screen.findByRole("list", { name: "Postings" }),
  ).findAllByRole("listitem");
  return app;
}

describe("KPI tiles", () => {
  it("show the count of every status in pipeline order", async () => {
    fakeJobsApi(csvJobs());
    await openJobs();
    const tiles = within(
      screen.getByRole("region", { name: "Postings per status" }),
    ).getAllByRole("button");
    expect(tiles.map((tile) => tile.getAttribute("aria-label"))).toEqual(
      Object.entries(CSV_COUNTS).map(
        ([status, count]) =>
          `${
            {
              found: "Found",
              shortlisted: "Shortlisted",
              cv_ready: "CV ready",
              staged: "Staged",
              applied: "Applied",
              interview: "Interview",
              rejected: "Rejected",
              closed: "Closed",
              skipped: "Skipped",
            }[status]
          }: ${count}`,
      ),
    );
    expect(screen.getByText("433 postings")).toBeInTheDocument();
  });

  it("a click filters on that status in the URL and the request; a second click clears it", async () => {
    const api = fakeJobsApi(csvJobs());
    const user = userEvent.setup();
    const { location } = await openJobs();
    await user.click(kpi("Applied"));
    await waitFor(() => {
      expect(location()).toBe("/jobs?status=applied");
    });
    await waitFor(() => {
      expect(lastRequest(api).getAll("status")).toEqual(["applied"]);
    });
    expect(kpi("Applied")).toHaveAttribute("aria-pressed", "true");
    expect(await screen.findByText("29 postings match")).toBeInTheDocument();
    // The tiles keep every count: they ignore the status filter.
    expect(kpi("Found")).toHaveAccessibleName("Found: 158");

    await user.click(kpi("Applied"));
    await waitFor(() => {
      expect(location()).toBe("/jobs");
    });
    expect(kpi("Applied")).toHaveAttribute("aria-pressed", "false");
  });

  it("a click on another tile replaces the status filter", async () => {
    fakeJobsApi(csvJobs());
    const user = userEvent.setup();
    const { location } = await openJobs("/jobs?status=found&status=staged");
    await user.click(kpi("Skipped"));
    await waitFor(() => {
      expect(location()).toBe("/jobs?status=skipped");
    });
  });

  it("shows a zero for a status the api leaves out", async () => {
    fakeJobsApi([makeJob(1)], {
      respond: () =>
        HttpResponse.json({
          items: [makeJob(1)],
          next_cursor: null,
          total: 1,
          counts_by_status: { found: 1 },
          fit_histogram: { buckets: [], unscored: 0 },
        }),
    });
    await openJobs();
    expect(kpi("Interview")).toHaveAccessibleName("Interview: 0");
  });
});

describe("fit histogram", () => {
  it("names each bucket with its range and count", async () => {
    const rows = [
      makeJob(1, { fit: 95 }),
      makeJob(2, { fit: 100 }),
      makeJob(3, { fit: 5 }),
      makeJob(4, { fit: null }),
    ];
    fakeJobsApi(rows);
    const user = userEvent.setup();
    await openJobs();
    // The chart sits in the Filters sheet, beside the fit range it sets.
    const sheet = await openFilters(user);
    const chart = within(sheet).getByRole("list", {
      name: "Postings per fit range",
    });
    const bars = within(chart).getAllByRole("button");
    expect(bars).toHaveLength(10);
    expect(bars[0]).toHaveAccessibleName("Fit 0 to 9: 1 postings");
    expect(bars[9]).toHaveAccessibleName("Fit 90 to 100: 2 postings");
    expect(within(sheet).getByText("1 unscored")).toBeInTheDocument();
  });

  it("a click on a bucket sets the fit range, a second click clears it", async () => {
    const api = fakeJobsApi(csvJobs());
    const user = userEvent.setup();
    const { location } = await openJobs();
    await openFilters(user);
    const bucket = () => screen.getByRole("button", { name: /^Fit 80 to 89:/ });
    await user.click(bucket());
    await waitFor(() => {
      expect(location()).toBe("/jobs?fit_min=80&fit_max=89");
    });
    await waitFor(() => {
      expect(lastRequest(api).get("fit_min")).toBe("80");
    });
    expect(lastRequest(api).get("fit_max")).toBe("89");
    expect(bucket()).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText("Fit from")).toHaveValue("80");
    expect(screen.getByText("Fit: 80 to 89")).toBeInTheDocument();
    await user.click(bucket());
    await waitFor(() => {
      expect(location()).toBe("/jobs");
    });
    expect(screen.getByLabelText("Fit from")).toHaveValue("");
  });
});

describe("filters and the URL", () => {
  it("sends every filter of the URL to the api", async () => {
    const api = fakeJobsApi(csvJobs());
    renderApp(
      "/jobs?q=engineer&status=shortlisted&type=CDI&company=Dust&fit_min=10&fit_max=90&found_from=2026-01-01&found_to=2026-12-31&sort=fit",
    );
    await waitFor(() => {
      expect(api.requests).toHaveLength(1);
    });
    const sent = api.requests[0]!;
    expect(sent.get("q")).toBe("engineer");
    expect(sent.getAll("status")).toEqual(["shortlisted"]);
    expect(sent.getAll("type")).toEqual(["CDI"]);
    expect(sent.get("company")).toBe("Dust");
    expect(sent.get("fit_min")).toBe("10");
    expect(sent.get("fit_max")).toBe("90");
    expect(sent.get("found_from")).toBe("2026-01-01");
    expect(sent.get("found_to")).toBe("2026-12-31");
    expect(sent.get("sort")).toBe("fit");
    expect(sent.get("limit")).toBe(String(PAGE_SIZE));
    expect(sent.get("cursor")).toBeNull();
    // The controls show the same view: the search and the chips in the toolbar, the rest in
    // the Filters sheet, whose button counts what it holds.
    expect(
      await screen.findByRole("searchbox", { name: "Search postings" }),
    ).toHaveValue("engineer");
    const chips = screen.getByRole("list", { name: "Active filters" });
    expect(within(chips).getAllByRole("listitem")).toHaveLength(6);
    const user = userEvent.setup();
    expect(
      screen.getByRole("button", { name: /^Filters\s*,\s*5 set$/ }),
    ).toBeInTheDocument();
    const sheet = await openFilters(user);
    expect(within(sheet).getByLabelText("Company")).toHaveValue("Dust");
    expect(within(sheet).getByLabelText("Sort")).toHaveValue("fit");
    expect(within(sheet).getByLabelText("Found from")).toHaveValue(
      "2026-01-01",
    );
    expect(within(sheet).getByLabelText("Fit from")).toHaveValue("10");
    expect(
      within(sheet).getByRole("button", { name: "Status: Shortlisted" }),
    ).toBeInTheDocument();
    expect(
      within(sheet).getByRole("button", { name: "Type: CDI" }),
    ).toBeInTheDocument();
  });

  it("ignores invalid values in the URL and never sends them", async () => {
    const api = fakeJobsApi(csvJobs());
    await openJobs(
      "/jobs?status=hired&type=Interim&fit_min=140&fit_max=x&found_from=2026-02-31&sort=random&cursor=abc&limit=9999",
    );
    const sent = api.requests[0]!;
    expect([...sent.keys()].sort()).toEqual(["limit", "sort"]);
    expect(sent.get("sort")).toBe("found_on");
    expect(sent.get("limit")).toBe(String(PAGE_SIZE));
    expect(screen.queryByRole("list", { name: "Active filters" })).toBeNull();
    expect(screen.getByText("433 postings")).toBeInTheDocument();
  });

  it("debounces the search box: one request after typing stops", async () => {
    const api = fakeJobsApi(csvJobs());
    const user = userEvent.setup();
    const { location } = await openJobs();
    const before = api.requests.length;
    await user.type(
      screen.getByRole("searchbox", { name: "Search postings" }),
      "Engineer 4",
    );
    // Typing ten characters sent nothing yet.
    expect(api.requests.length).toBe(before);
    expect(location()).toBe("/jobs");
    await waitFor(() => {
      expect(location()).toBe("/jobs?q=Engineer+4");
    });
    await waitFor(() => {
      expect(api.requests.length).toBe(before + 1);
    });
    expect(lastRequest(api).get("q")).toBe("Engineer 4");
    await new Promise((resolve) =>
      setTimeout(resolve, SEARCH_DEBOUNCE_MS + 100),
    );
    expect(api.requests.length).toBe(before + 1);
  });

  it("Enter in the search box commits at once", async () => {
    fakeJobsApi(csvJobs());
    const user = userEvent.setup();
    const { location } = await openJobs();
    await user.type(
      screen.getByRole("searchbox", { name: "Search postings" }),
      "agents{Enter}",
    );
    expect(location()).toBe("/jobs?q=agents");
  });

  it("debounces the company field", async () => {
    const api = fakeJobsApi(csvJobs());
    const user = userEvent.setup();
    const { location } = await openJobs();
    await openFilters(user);
    await user.type(screen.getByLabelText("Company"), "Dust");
    expect(location()).toBe("/jobs");
    await waitFor(() => {
      expect(location()).toBe("/jobs?company=Dust");
    });
    await waitFor(() => {
      expect(lastRequest(api).get("company")).toBe("Dust");
    });
  });

  it("selects several statuses and types from the menus", async () => {
    fakeJobsApi(csvJobs());
    const user = userEvent.setup();
    const { location } = await openJobs();
    await openFilters(user);
    await user.click(screen.getByRole("button", { name: "Status: All" }));
    await user.click(screen.getByRole("menuitemcheckbox", { name: "Staged" }));
    await user.click(screen.getByRole("menuitemcheckbox", { name: "Found" }));
    await waitFor(() => {
      expect(location()).toBe("/jobs?status=found&status=staged");
    });
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: "Type: All" }));
    await user.click(screen.getByRole("menuitemcheckbox", { name: "VIE" }));
    await waitFor(() => {
      expect(location()).toBe("/jobs?status=found&status=staged&type=VIE");
    });
  });

  it("commits a fit range typed by hand and refuses an inverted one", async () => {
    fakeJobsApi(csvJobs());
    const user = userEvent.setup();
    const { location } = await openJobs();
    await openFilters(user);
    await user.type(screen.getByLabelText("Fit from"), "90");
    await user.type(screen.getByLabelText("Fit to"), "40{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The lowest fit is above the highest.",
    );
    // Leaving "Fit from" committed it alone; the inverted pair was not sent.
    expect(location()).toBe("/jobs?fit_min=90");
    expect(screen.getByLabelText("Fit to")).toHaveFocus();
    await user.clear(screen.getByLabelText("Fit to"));
    await user.type(screen.getByLabelText("Fit to"), "100{Enter}");
    await waitFor(() => {
      expect(location()).toBe("/jobs?fit_min=90&fit_max=100");
    });
    await user.clear(screen.getByLabelText("Fit from"));
    await user.type(screen.getByLabelText("Fit from"), "abc{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Fit is a whole number from 0 to 100.",
    );
  });

  it("sets found dates and refuses a range the wrong way round", async () => {
    fakeJobsApi(csvJobs());
    const { location } = await openJobs();
    await openFilters(userEvent.setup());
    fireEvent.change(screen.getByLabelText("Found from"), {
      target: { value: "2026-03-01" },
    });
    await waitFor(() => {
      expect(location()).toBe("/jobs?found_from=2026-03-01");
    });
    fireEvent.change(screen.getByLabelText("Found to"), {
      target: { value: "2026-02-01" },
    });
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The first date is after the second.",
    );
    expect(location()).toBe("/jobs?found_from=2026-03-01");
  });

  it("changes the sort", async () => {
    const api = fakeJobsApi(csvJobs());
    const user = userEvent.setup();
    const { location } = await openJobs();
    await openFilters(user);
    await user.selectOptions(screen.getByLabelText("Sort"), "company");
    await waitFor(() => {
      expect(location()).toBe("/jobs?sort=company");
    });
    await waitFor(() => {
      expect(lastRequest(api).get("sort")).toBe("company");
    });
  });

  it("removes one chip, then Clear filters removes the rest but keeps the sort", async () => {
    fakeJobsApi(csvJobs());
    const user = userEvent.setup();
    const { location } = await openJobs(
      "/jobs?q=engineer&status=found&sort=fit",
    );
    await user.click(
      screen.getByRole("button", { name: "Remove filter Search: engineer" }),
    );
    await waitFor(() => {
      expect(location()).toBe("/jobs?status=found&sort=fit");
    });
    expect(
      screen.getByRole("searchbox", { name: "Search postings" }),
    ).toHaveValue("");
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => {
      expect(location()).toBe("/jobs?sort=fit");
    });
  });

  it("back and forward step through the views and refill the controls", async () => {
    fakeJobsApi(csvJobs());
    const user = userEvent.setup();
    const { location, router } = await openJobs();
    await user.click(kpi("Staged"));
    await waitFor(() => {
      expect(location()).toBe("/jobs?status=staged");
    });
    await user.type(
      screen.getByRole("searchbox", { name: "Search postings" }),
      "Engineer{Enter}",
    );
    expect(location()).toBe("/jobs?q=Engineer&status=staged");
    await act(async () => {
      await router.navigate(-1);
    });
    expect(location()).toBe("/jobs?status=staged");
    expect(
      screen.getByRole("searchbox", { name: "Search postings" }),
    ).toHaveValue("");
    await act(async () => {
      await router.navigate(-1);
    });
    expect(location()).toBe("/jobs");
    expect(kpi("Staged")).toHaveAttribute("aria-pressed", "false");
    await act(async () => {
      await router.navigate(1);
    });
    expect(kpi("Staged")).toHaveAttribute("aria-pressed", "true");
  });
});

describe("list", () => {
  it("renders a window of items, not all 433, each saying where it is in the whole", async () => {
    fakeJobsApi(csvJobs());
    await openJobs();
    const rows = dataRows();
    expect(rows.length).toBeGreaterThan(5);
    expect(rows.length).toBeLessThan(60);
    expect(rows[0]).toHaveAttribute("aria-setsize", "433");
    expect(rows[0]).toHaveAttribute("aria-posinset", "1");
    expect(rows[0]).toHaveTextContent("Engineer 1");
  });

  it("loads the next page with next_cursor while the list scrolls, and stops at null", async () => {
    const api = fakeJobsApi(csvJobs());
    await openJobs();
    expect(api.requests).toHaveLength(1);
    for (let page = 1; page <= 4; page += 1) {
      scrollListTo(page * PAGE_SIZE * ITEM_ESTIMATE);
      await waitFor(() => {
        expect(api.requests).toHaveLength(page + 1);
      });
      expect(lastRequest(api).get("cursor")).toBe(String(page * PAGE_SIZE));
      await screen.findByText(`Engineer ${page * PAGE_SIZE + 1}`);
    }
    scrollListTo(433 * ITEM_ESTIMATE);
    await screen.findByText("Engineer 433");
    await new Promise((resolve) => setTimeout(resolve, 100));
    // 433 rows are five pages; the fifth said next_cursor null, so nothing more is asked.
    expect(api.requests).toHaveLength(5);
    expect(
      screen.queryByText(/Load more postings|Loading more postings/),
    ).toBeNull();
    // Still a window, even with everything loaded.
    expect(dataRows().length).toBeLessThan(60);
  });

  it("offers a retry when a next page fails", async () => {
    let fail = true;
    const api = fakeJobsApi(csvJobs(), {
      respond: (params) =>
        params.get("cursor") && fail
          ? HttpResponse.json({ detail: "boom" }, { status: 500 })
          : undefined,
    });
    const user = userEvent.setup();
    await openJobs();
    scrollListTo(PAGE_SIZE * ITEM_ESTIMATE);
    expect(
      await screen.findByText(/More postings could not be loaded/),
    ).toBeInTheDocument();
    const failed = api.requests.length;
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(api.requests.length).toBe(failed);
    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByText("Engineer 101");
  });

  it("shows skeleton rows while the first page loads", async () => {
    fakeJobsApi(csvJobs(), { latencyMs: 200 });
    renderApp("/jobs");
    expect(
      await screen.findByRole("status", { name: "Loading postings" }),
    ).toBeInTheDocument();
    await within(
      await screen.findByRole("list", { name: "Postings" }),
    ).findAllByRole("listitem");
    expect(
      screen.queryByRole("status", { name: "Loading postings" }),
    ).toBeNull();
  });

  it("says when there are no postings at all, with Find jobs and Add a posting by hand only there", async () => {
    fakeJobsApi([]);
    const user = userEvent.setup();
    renderApp("/jobs");
    const empty = await screen.findByRole("region", {
      name: "No postings yet.",
    });
    expect(empty).toHaveTextContent(
      "Tell the app where to look and it brings postings here.",
    );
    expect(screen.queryByRole("list", { name: "Postings" })).toBeNull();
    // Nothing to count or filter: the tiles and the toolbar are left out, and no pane is empty.
    expect(
      screen.queryByRole("region", { name: "Postings per status" }),
    ).toBeNull();
    expect(screen.queryByRole("search")).toBeNull();
    expect(screen.queryByRole("button", { name: /^Filters/ })).toBeNull();
    expect(
      screen.queryByRole("region", { name: "Selected posting" }),
    ).toBeNull();
    // The empty list holds the page's Find jobs and Add a posting; the header does not repeat them.
    expect(screen.getAllByRole("button", { name: "Find jobs" })).toEqual([
      within(empty).getByRole("button", { name: "Find jobs" }),
    ]);
    expect(screen.queryByRole("button", { name: "Add a posting" })).toBeNull();
    expect(screen.getByRole("button", { name: "Past searches" })).toBeVisible();
    await user.click(
      within(empty).getByRole("button", { name: "Add a posting by hand" }),
    );
    expect(
      await screen.findByRole("dialog", { name: "Add a posting" }),
    ).toBeInTheDocument();
  });

  it("keeps the tiles, the toolbar and the chart when a filter hides every posting", async () => {
    fakeJobsApi(csvJobs());
    const user = userEvent.setup();
    renderApp("/jobs?q=nothing+like+this");
    await screen.findByText("No postings match these filters");
    expect(
      screen.getByRole("region", { name: "Postings per status" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("search", { name: "Filter postings" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "No postings yet." }),
    ).toBeNull();
    expect(
      screen.queryByRole("region", { name: "Selected posting" }),
    ).toBeNull();
    expect(screen.getByRole("button", { name: "Find jobs" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Add a posting" })).toBeVisible();
    const sheet = await openFilters(user);
    expect(
      within(sheet).getByRole("region", { name: "Fit distribution" }),
    ).toBeInTheDocument();
  });

  it("writes counts with thousands separators", async () => {
    fakeJobsApi([makeJob(1)], {
      respond: () =>
        HttpResponse.json({
          items: [makeJob(1)],
          next_cursor: null,
          total: 14115,
          counts_by_status: { ...CSV_COUNTS, found: 12840, applied: 1029 },
          fit_histogram: {
            buckets: Array.from({ length: 10 }, (_, i) => ({
              low: i * 10,
              high: i === 9 ? 100 : i * 10 + 9,
              count: i === 5 ? 5100 : 0,
            })),
            unscored: 1234,
          },
        }),
    });
    const user = userEvent.setup();
    renderApp("/jobs");
    expect(await screen.findByText("14,115 postings")).toBeInTheDocument();
    expect(kpi("Found")).toHaveAccessibleName("Found: 12,840");
    expect(kpi("Found")).toHaveTextContent("12,840");
    expect(kpi("Applied")).toHaveTextContent("1,029");
    const sheet = await openFilters(user);
    expect(
      within(sheet).getByRole("button", { name: "Show 14,115 postings" }),
    ).toBeInTheDocument();
    const chart = within(sheet).getByRole("region", {
      name: "Fit distribution",
    });
    expect(
      within(chart).getByRole("button", {
        name: "Fit 50 to 59: 5,100 postings",
      }),
    ).toHaveTextContent("5,100");
    expect(chart).toHaveTextContent("1,234 unscored");
  });

  it("shows the tiles as unavailable, not loading, when the list cannot be loaded", async () => {
    fakeJobsApi([], {
      respond: () =>
        HttpResponse.json({ detail: "database down" }, { status: 503 }),
    });
    renderApp("/jobs");
    await screen.findByText(/The postings could not be loaded\./);
    const tiles = within(
      screen.getByRole("region", { name: "Postings per status" }),
    ).getAllByRole("button");
    expect(tiles).toHaveLength(9);
    for (const tile of tiles) {
      expect(tile.getAttribute("aria-label")).toMatch(/: unavailable$/);
      expect(tile).toHaveTextContent("Unavailable");
    }
  });

  it("says when no posting matches, with a way out", async () => {
    fakeJobsApi(csvJobs());
    const user = userEvent.setup();
    const { location } = renderApp("/jobs?q=nothing+like+this");
    expect(
      await screen.findByText("No postings match these filters"),
    ).toBeInTheDocument();
    const empty = screen
      .getByText("No postings match these filters")
      .closest<HTMLElement>("[data-slot=empty]")!;
    await user.click(
      within(empty).getByRole("button", { name: "Clear filters" }),
    );
    await waitFor(() => {
      expect(location()).toBe("/jobs");
    });
    await within(
      await screen.findByRole("list", { name: "Postings" }),
    ).findAllByRole("listitem");
  });

  it("reports an error and retries", async () => {
    let fail = true;
    const api = fakeJobsApi(csvJobs(), {
      respond: () =>
        fail
          ? HttpResponse.json(
              { detail: "The database is busy. Try again in a moment." },
              { status: 503 },
            )
          : undefined,
    });
    const user = userEvent.setup();
    renderApp("/jobs");
    expect(
      await screen.findByText(
        "The postings could not be loaded. The database is busy. Try again in a moment.",
      ),
    ).toBeInTheDocument();
    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await within(
      await screen.findByRole("list", { name: "Postings" }),
    ).findAllByRole("listitem");
    expect(api.requests).toHaveLength(2);
  });

  it("reports a body it cannot read as an error, not a crash", async () => {
    fakeJobsApi([], {
      respond: () => HttpResponse.json({ items: "nope" }),
    });
    renderApp("/jobs");
    expect(
      await screen.findByText(
        /The server sent an answer this page cannot read\./,
      ),
    ).toBeInTheDocument();
  });

  it("shows a fallback label for a status this build does not know", async () => {
    fakeJobsApi([makeJob(1, { status: "on_hold", contract_type: "Interim" })]);
    await openJobs();
    const row = dataRows()[0]!;
    expect(within(row).getByText("On hold")).toBeInTheDocument();
    expect(within(row).getByText("Paris, France, Interim")).toBeInTheDocument();
  });

  it("renders hostile company and title text as text", async () => {
    fakeJobsApi([
      makeJob(1, {
        company: "<img src=x onerror=alert(1)>",
        title: "<script>window.__xss=1</script>",
      }),
    ]);
    const { container } = await openJobs();
    expect(
      screen.getByText("<script>window.__xss=1</script>"),
    ).toBeInTheDocument();
    expect(container.querySelector("img, script")).toBeNull();
  });

  it("shows fit badges on the colour scale and Unscored for no fit", async () => {
    fakeJobsApi([
      makeJob(1, { fit: 92 }),
      makeJob(2, { fit: 40 }),
      makeJob(3, { fit: null }),
    ]);
    await openJobs();
    const [a, b, c] = dataRows();
    expect(within(a!).getByLabelText("Fit 92 out of 100")).toHaveAttribute(
      "data-tone",
      "top",
    );
    expect(within(b!).getByLabelText("Fit 40 out of 100")).toHaveAttribute(
      "data-tone",
      "low",
    );
    expect(within(c!).getByText("Unscored")).toHaveAttribute(
      "data-tone",
      "none",
    );
  });
});

describe("the selected posting beside the list", () => {
  it("shows the first posting when the URL names none, marked in the list", async () => {
    fakeJobsApi(csvJobs());
    await openJobs();
    const [first, second] = dataRows();
    expect(first).toHaveAttribute("aria-current", "true");
    expect(second).not.toHaveAttribute("aria-current");
    expect(
      await within(pane()).findByRole("heading", {
        level: 2,
        name: "Engineer 1",
      }),
    ).toBeVisible();
    // The page keeps one h1: the posting's title is the pane's heading.
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  it("shows the posting the URL names, so a reload or a shared link opens on it", async () => {
    fakeJobsApi(csvJobs());
    const { location } = await openJobs(`/jobs?status=found&job=${idOf(3)}`);
    expect(location()).toBe(`/jobs?status=found&job=${idOf(3)}`);
    expect(
      await within(pane()).findByRole("heading", {
        level: 2,
        name: "Engineer 3",
      }),
    ).toBeVisible();
    const current = dataRows().filter(
      (item) => item.getAttribute("aria-current") === "true",
    );
    expect(current).toHaveLength(1);
    expect(current[0]).toHaveTextContent("Engineer 3");
    // The current posting is the list's tab stop.
    expect(current[0]).toHaveAttribute("tabindex", "0");
  });

  it("shows a posting the list has not loaded yet, and ignores a job that is no id", async () => {
    fakeJobsApi(csvJobs());
    await openJobs(`/jobs?job=${idOf(400)}`);
    expect(
      await within(pane()).findByRole("heading", { name: "Engineer 400" }),
    ).toBeVisible();
    expect(
      dataRows().filter((item) => item.hasAttribute("aria-current")),
    ).toEqual([]);
    cleanup();
    await openJobs("/jobs?job=../../admin");
    expect(
      await within(pane()).findByRole("heading", { name: "Engineer 1" }),
    ).toBeVisible();
  });

  it("a click on an item selects it: the URL names it, the list marks it and the pane shows it", async () => {
    fakeJobsApi(csvJobs());
    const user = userEvent.setup();
    const { location } = await openJobs("/jobs?status=found");
    await user.click(within(dataRows()[1]!).getByText("Remote, Permanent"));
    expect(location()).toBe(`/jobs?status=found&job=${idOf(2)}`);
    expect(dataRows()[1]).toHaveAttribute("aria-current", "true");
    expect(dataRows()[0]).not.toHaveAttribute("aria-current");
    expect(
      await within(pane()).findByRole("heading", {
        level: 2,
        name: "Engineer 2",
      }),
    ).toBeVisible();
  });

  it("a click on the title opens /jobs/:id, and its Back returns to the same view and posting", async () => {
    fakeJobsApi(csvJobs());
    const user = userEvent.setup();
    const { location } = await openJobs("/jobs?status=found");
    await user.click(
      within(dataRows()[1]!).getByRole("link", { name: "Engineer 2" }),
    );
    expect(location()).toBe(`/jobs/${idOf(2)}`);
    await screen.findByRole("heading", { level: 1, name: "Engineer 2" });
    expect(screen.getByRole("link", { name: "Back to jobs" })).toHaveAttribute(
      "href",
      `/jobs?status=found&job=${idOf(2)}`,
    );
  });

  it("links the pane's posting to its full page", async () => {
    fakeJobsApi(csvJobs());
    const user = userEvent.setup();
    const { location } = await openJobs(`/jobs?job=${idOf(2)}`);
    const full = await within(pane()).findByRole("link", {
      name: "Open full page",
    });
    expect(full).toHaveAttribute("href", `/jobs/${idOf(2)}`);
    await user.click(full);
    expect(location()).toBe(`/jobs/${idOf(2)}`);
    expect(screen.getByRole("link", { name: "Back to jobs" })).toHaveAttribute(
      "href",
      `/jobs?job=${idOf(2)}`,
    );
  });

  it("arrow keys move the selection, the pane follows, and Enter opens the posting", async () => {
    fakeJobsApi(csvJobs());
    const user = userEvent.setup();
    const { location } = await openJobs();
    const rows = dataRows();
    expect(rows[0]).toHaveAttribute("tabindex", "0");
    expect(rows[1]).toHaveAttribute("tabindex", "-1");
    rows[0]!.focus();
    await user.keyboard("{ArrowDown}{ArrowDown}");
    await waitFor(() => {
      expect(document.activeElement).toBe(dataRows()[2]);
    });
    expect(location()).toBe(`/jobs?job=${idOf(3)}`);
    expect(dataRows()[2]).toHaveAttribute("aria-current", "true");
    expect(
      await within(pane()).findByRole("heading", {
        level: 2,
        name: "Engineer 3",
      }),
    ).toBeVisible();
    await user.keyboard("{ArrowUp}");
    await waitFor(() => {
      expect(document.activeElement).toBe(dataRows()[1]);
    });
    expect(
      await within(pane()).findByRole("heading", { name: "Engineer 2" }),
    ).toBeVisible();
    await user.keyboard("{Home}");
    await waitFor(() => {
      expect(document.activeElement).toBe(dataRows()[0]);
    });
    await user.keyboard("{ArrowUp}");
    expect(document.activeElement).toBe(dataRows()[0]);
    expect(location()).toBe(`/jobs?job=${idOf(1)}`);
    await user.keyboard("{ArrowDown}{Enter}");
    expect(location()).toBe(`/jobs/${idOf(2)}`);
  });

  it("moving through the list replaces the history entry: back leaves the list", async () => {
    fakeJobsApi(csvJobs());
    const user = userEvent.setup();
    const { location, router } = await openJobs("/jobs?status=found");
    await user.click(kpi("Found"));
    await waitFor(() => {
      expect(location()).toBe("/jobs");
    });
    dataRows()[0]!.focus();
    await user.keyboard("{ArrowDown}{ArrowDown}");
    await waitFor(() => {
      expect(location()).toBe(`/jobs?job=${idOf(3)}`);
    });
    await act(async () => {
      await router.navigate(-1);
    });
    expect(location()).toBe("/jobs?status=found");
  });

  it("a filter change starts the new view on its first posting", async () => {
    fakeJobsApi(csvJobs());
    const user = userEvent.setup();
    const { location } = await openJobs(`/jobs?job=${idOf(3)}`);
    await user.click(kpi("Shortlisted"));
    await waitFor(() => {
      expect(location()).toBe("/jobs?status=shortlisted");
    });
    await waitFor(() => {
      expect(dataRows()[0]).toHaveAttribute("aria-current", "true");
    });
    expect(dataRows()[0]).toHaveTextContent("Engineer 159");
  });

  it("only one item is in the tab order", async () => {
    fakeJobsApi(csvJobs());
    await openJobs();
    const tabbable = dataRows().filter(
      (row) => row.getAttribute("tabindex") === "0",
    );
    expect(tabbable).toHaveLength(1);
    for (const link of within(list()).getAllByRole("link"))
      expect(link).toHaveAttribute("tabindex", "-1");
  });
});

describe("on a phone", () => {
  it("shows the list alone, and a tap opens the posting's page", async () => {
    phoneScreen();
    fakeJobsApi(csvJobs());
    server.use(
      http.get("*/api/jobs/:id", async () => {
        await delay("infinite");
        return HttpResponse.json({});
      }),
    );
    const user = userEvent.setup();
    const { location } = await openJobs("/jobs?status=found");
    expect(
      screen.queryByRole("region", { name: "Selected posting" }),
    ).toBeNull();
    expect(
      dataRows().filter((item) => item.hasAttribute("aria-current")),
    ).toEqual([]);
    await user.click(within(dataRows()[1]!).getByText("Remote, Permanent"));
    expect(location()).toBe(`/jobs/${idOf(2)}`);
  });

  it("opens the filters in a sheet from one button that counts them, the chart with them", async () => {
    phoneScreen();
    fakeJobsApi(csvJobs());
    const user = userEvent.setup();
    const { location } = renderApp("/jobs?status=found&type=CDI&q=Engineer");
    // The search shows itself in the toolbar: the button counts what its sheet holds.
    const toggle = await screen.findByRole("button", {
      name: /^Filters\s*,\s*2 set$/,
    });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(
      screen.getByRole("searchbox", { name: "Search postings" }),
    ).toHaveValue("Engineer");
    // The tiles stay, as one row.
    expect(
      await screen.findByRole("button", { name: /^Found: \d/ }),
    ).toBeVisible();

    await user.click(toggle);
    const sheet = await screen.findByRole("dialog", { name: "Filters" });
    expect(
      within(sheet).getByRole("region", { name: "Fit distribution" }),
    ).toBeInTheDocument();
    await user.click(within(sheet).getByRole("button", { name: "Type: CDI" }));
    await user.click(screen.getByRole("menuitemcheckbox", { name: "CDI" }));
    await waitFor(() => {
      expect(location()).toBe("/jobs?q=Engineer&status=found");
    });
    await user.keyboard("{Escape}");
    expect(toggle).toHaveAccessibleName(/^Filters\s*,\s*1 set$/);
    await user.click(
      within(sheet).getByRole("button", { name: /^Show [\d,]+ postings?$/ }),
    );
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Filters" })).toBeNull();
    });
  });

  it("names no count on the Filters button while no filter is set", async () => {
    phoneScreen();
    fakeJobsApi(csvJobs());
    renderApp("/jobs?q=Engineer");
    expect(
      await screen.findByRole("button", { name: "Filters" }),
    ).toHaveAttribute("aria-expanded", "false");
  });
});

describe("copy", () => {
  it("uses no dashes as separators on the dashboard", async () => {
    fakeJobsApi(csvJobs());
    const { container } = await openJobs(
      "/jobs?q=Engineer&status=found&fit_min=0&fit_max=100&found_from=2026-01-01&found_to=2026-12-31&type=CDI&company=Mistral",
    );
    expect(container.textContent).not.toMatch(/[\u2013\u2014]| - /);
  });
});
