import {
  act,
  fireEvent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { admin, fakeSession } from "@/test/auth";
import { csvJobs, CSV_COUNTS, fakeJobsApi, makeJob } from "@/test/jobs";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import { PAGE_SIZE } from "./api";
import { SEARCH_DEBOUNCE_MS } from "./filter-bar";

const kpi = (label: string) =>
  screen.getByRole("button", { name: new RegExp(`^${label}: `) });
const table = () => screen.getByRole("table", { name: "Postings" });
const dataRows = () =>
  within(table())
    .getAllByRole("row")
    .filter((row) => row.hasAttribute("data-index"));
const lastRequest = (state: { requests: URLSearchParams[] }) =>
  state.requests.at(-1)!;

/** Scroll the window (jsdom does not), so the window virtualiser sees it. */
function scrollWindowTo(y: number) {
  Object.defineProperty(window, "scrollY", { value: y, configurable: true });
  Object.defineProperty(window, "pageYOffset", {
    value: y,
    configurable: true,
  });
  fireEvent.scroll(window);
}

beforeEach(() => {
  fakeSession(admin);
  scrollWindowTo(0);
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
});

async function openJobs(path = "/jobs") {
  const app = renderApp(path);
  await screen.findByRole("heading", { level: 1, name: "Jobs" });
  await within(
    await screen.findByRole("table", { name: "Postings" }),
  ).findAllByRole("row");
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
    await openJobs();
    const chart = screen.getByRole("list", { name: "Postings per fit range" });
    const bars = within(chart).getAllByRole("button");
    expect(bars).toHaveLength(10);
    expect(bars[0]).toHaveAccessibleName("Fit 0 to 9: 1 postings");
    expect(bars[9]).toHaveAccessibleName("Fit 90 to 100: 2 postings");
    expect(screen.getByText("1 unscored")).toBeInTheDocument();
  });

  it("a click on a bucket sets the fit range, a second click clears it", async () => {
    const api = fakeJobsApi(csvJobs());
    const user = userEvent.setup();
    const { location } = await openJobs();
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
    // The controls show the same view.
    expect(
      screen.getByRole("searchbox", { name: "Search postings" }),
    ).toHaveValue("engineer");
    expect(screen.getByLabelText("Company")).toHaveValue("Dust");
    expect(screen.getByLabelText("Sort")).toHaveValue("fit");
    expect(screen.getByLabelText("Found from")).toHaveValue("2026-01-01");
    expect(
      screen.getByRole("button", { name: "Status: Shortlisted" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Type: CDI" }),
    ).toBeInTheDocument();
    const chips = screen.getByRole("list", { name: "Active filters" });
    expect(within(chips).getAllByRole("listitem")).toHaveLength(6);
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
  it("renders a window of rows, not all 433", async () => {
    fakeJobsApi(csvJobs());
    await openJobs();
    const rows = dataRows();
    expect(rows.length).toBeGreaterThan(5);
    expect(rows.length).toBeLessThan(60);
    expect(table()).toHaveAttribute("aria-rowcount", "434");
    expect(rows[0]).toHaveTextContent("Engineer 1");
  });

  it("loads the next page with next_cursor while scrolling, and stops at null", async () => {
    const api = fakeJobsApi(csvJobs());
    await openJobs();
    expect(api.requests).toHaveLength(1);
    for (let page = 1; page <= 4; page += 1) {
      scrollWindowTo(page * PAGE_SIZE * 48);
      await waitFor(() => {
        expect(api.requests).toHaveLength(page + 1);
      });
      expect(lastRequest(api).get("cursor")).toBe(String(page * PAGE_SIZE));
      await screen.findByText(`Engineer ${page * PAGE_SIZE + 1}`);
    }
    scrollWindowTo(433 * 48);
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
    scrollWindowTo(PAGE_SIZE * 48);
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
      await screen.findByRole("table", { name: "Postings" }),
    ).findAllByRole("row");
    expect(
      screen.queryByRole("status", { name: "Loading postings" }),
    ).toBeNull();
  });

  it("says when there are no postings at all", async () => {
    fakeJobsApi([]);
    renderApp("/jobs");
    expect(await screen.findByText("No postings yet")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("says when no posting matches, with a way out", async () => {
    fakeJobsApi(csvJobs());
    const user = userEvent.setup();
    const { location } = renderApp("/jobs?q=nothing+like+this");
    expect(
      await screen.findByText("No postings match these filters"),
    ).toBeInTheDocument();
    const empty = screen.getByText(
      "No postings match these filters",
    ).parentElement!;
    await user.click(
      within(empty).getByRole("button", { name: "Clear filters" }),
    );
    await waitFor(() => {
      expect(location()).toBe("/jobs");
    });
    await within(
      await screen.findByRole("table", { name: "Postings" }),
    ).findAllByRole("row");
  });

  it("reports an error and retries", async () => {
    let fail = true;
    const api = fakeJobsApi(csvJobs(), {
      respond: () =>
        fail
          ? HttpResponse.json({ detail: "database down" }, { status: 503 })
          : undefined,
    });
    const user = userEvent.setup();
    renderApp("/jobs");
    expect(
      await screen.findByText(
        "The postings could not be loaded. The API answered 503: database down.",
      ),
    ).toBeInTheDocument();
    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await within(
      await screen.findByRole("table", { name: "Postings" }),
    ).findAllByRole("row");
    expect(api.requests).toHaveLength(2);
  });

  it("reports a body it cannot read as an error, not a crash", async () => {
    fakeJobsApi([], {
      respond: () => HttpResponse.json({ items: "nope" }),
    });
    renderApp("/jobs");
    expect(
      await screen.findByText(
        /The API sent a response this page cannot read\./,
      ),
    ).toBeInTheDocument();
  });

  it("shows a fallback label for a status this build does not know", async () => {
    fakeJobsApi([makeJob(1, { status: "on_hold", contract_type: "Interim" })]);
    await openJobs();
    const row = dataRows()[0]!;
    expect(within(row).getByText("On hold")).toBeInTheDocument();
    expect(within(row).getByText("Interim")).toBeInTheDocument();
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

describe("opening a posting", () => {
  it("a row click opens /jobs/:id", async () => {
    fakeJobsApi(csvJobs());
    server.use(
      http.get("*/api/jobs/:id", async () => {
        await delay("infinite");
        return HttpResponse.json({});
      }),
    );
    const user = userEvent.setup();
    const { location } = await openJobs("/jobs?status=found");
    await user.click(within(dataRows()[1]!).getByText("Remote"));
    expect(location()).toBe("/jobs/00000000-0000-4000-9000-000000000002");
  });

  it("arrow keys move between rows and Enter opens one", async () => {
    fakeJobsApi(csvJobs());
    server.use(
      http.get("*/api/jobs/:id", async () => {
        await delay("infinite");
        return HttpResponse.json({});
      }),
    );
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
    await user.keyboard("{ArrowUp}");
    await waitFor(() => {
      expect(document.activeElement).toBe(dataRows()[1]);
    });
    await user.keyboard("{Home}");
    await waitFor(() => {
      expect(document.activeElement).toBe(dataRows()[0]);
    });
    await user.keyboard("{ArrowUp}");
    expect(document.activeElement).toBe(dataRows()[0]);
    await user.keyboard("{ArrowDown}{Enter}");
    expect(location()).toBe("/jobs/00000000-0000-4000-9000-000000000002");
  });

  it("only one row is in the tab order", async () => {
    fakeJobsApi(csvJobs());
    await openJobs();
    const tabbable = dataRows().filter(
      (row) => row.getAttribute("tabindex") === "0",
    );
    expect(tabbable).toHaveLength(1);
    for (const link of within(table()).getAllByRole("link"))
      expect(link).toHaveAttribute("tabindex", "-1");
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
