import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { admin, fakeSession } from "@/test/auth";
import { fakeJobsApi, makeJob } from "@/test/jobs";
import { renderApp } from "@/test/render";
import { fakeSearches, makeSearch } from "@/test/searches";
import { server } from "@/test/server";

const SWEEP = "0b6c3a52-6d1e-4c4f-9f43-2a41c1a1e0aa";

function setUp() {
  fakeSession(admin);
  const rows = [
    { ...makeJob(1), location: "Saint-Denis, La Réunion" },
    { ...makeJob(2), location: "Paris" },
    { ...makeJob(3), location: "Paris, France" },
  ];
  const api = fakeJobsApi(rows);
  const facets: URLSearchParams[] = [];
  server.use(
    http.get("*/api/jobs/facets", ({ request }) => {
      facets.push(new URL(request.url).searchParams);
      return HttpResponse.json({
        places: [
          { value: "Paris", count: 2 },
          { value: "La Réunion", count: 1 },
        ],
        sources: [
          { value: "web", count: 2 },
          { value: "www.welcometothejungle.com", count: 1 },
        ],
      });
    }),
  );
  return { api, facets };
}

async function openSheet(path = "/jobs") {
  const app = renderApp(path);
  const user = userEvent.setup();
  await screen.findByRole(
    "heading",
    { level: 1, name: "Jobs" },
    { timeout: 10_000 },
  );
  await user.click(await screen.findByRole("button", { name: /^Filters/ }));
  const sheet = await screen.findByRole("dialog", { name: "Filters" });
  await within(sheet).findByLabelText("Found from");
  return { app, user, sheet };
}

describe("the Filters panel of jobs J29", () => {
  it("lists the filters in the order a person narrows a list", async () => {
    setUp();
    const { sheet } = await openSheet();
    const names = within(sheet)
      .getAllByRole("button")
      .map((button) => button.getAttribute("aria-label") ?? button.textContent)
      .filter((name) =>
        /^(Place|Work mode|Contract|Website|Application|Status):/.test(name),
      )
      .map((name) => name.split(":")[0]);
    expect(names).toEqual([
      "Place",
      "Work mode",
      "Contract",
      "Website",
      "Application",
      "Status",
    ]);
    const labels = [
      within(sheet).getByLabelText("Published"),
      within(sheet).getByLabelText("Pay"),
      within(sheet).getByLabelText("Fit from"),
      within(sheet).getByLabelText("Found from"),
      within(sheet).getByLabelText("Company"),
      within(sheet).getByLabelText("Sort"),
    ];
    for (const [before, after] of labels
      .slice(0, -1)
      .map((l, i) => [l, labels[i + 1]!])) {
      expect(
        before!.compareDocumentPosition(after!) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    }
  });

  it("puts a picked place in the address, as a chip, and the Show count follows", async () => {
    const { api, facets } = setUp();
    const { app, user, sheet } = await openSheet();
    expect(
      within(sheet).getByRole("button", { name: "Show 3 postings" }),
    ).toBeInTheDocument();
    await user.click(within(sheet).getByRole("button", { name: "Place: All" }));
    const menu = await screen.findByRole("menu");
    expect(
      within(menu).getByRole("menuitemcheckbox", { name: /Paris\s*2/ }),
    ).toBeInTheDocument();
    await user.click(
      within(menu).getByRole("menuitemcheckbox", { name: /La Réunion/ }),
    );
    await user.keyboard("{Escape}");
    expect(app.location()).toBe("/jobs?place=La+R%C3%A9union");
    expect(api.requests.at(-1)?.getAll("place")).toEqual(["La Réunion"]);
    expect(
      await within(sheet).findByRole("button", { name: "Show 1 posting" }),
    ).toBeInTheDocument();
    // The facets are asked under the filters; the api leaves out each facet's own.
    expect(facets.at(-1)?.getAll("place")).toEqual(["La Réunion"]);
    await user.click(
      within(sheet).getByRole("button", { name: "Show 1 posting" }),
    );
    const chips = await screen.findByRole("list", { name: "Active filters" });
    expect(within(chips).getByText("Place: La Réunion")).toBeInTheDocument();
  });

  it("adds a typed place and refuses one without a letter", async () => {
    setUp();
    const { app, user, sheet } = await openSheet();
    const field = within(sheet).getByLabelText("Another place");
    await user.type(field, ",,{Enter}");
    expect(
      within(sheet).getByText(/A place needs a letter or a digit/),
    ).toBeInTheDocument();
    await user.clear(field);
    await user.type(field, "Papeete{Enter}");
    expect(app.location()).toBe("/jobs?place=Papeete");
  });

  it("keeps work mode, website, published, pay and application in the address", async () => {
    setUp();
    const { app, user, sheet } = await openSheet();
    await user.click(
      within(sheet).getByRole("button", { name: "Work mode: All" }),
    );
    await user.click(
      await screen.findByRole("menuitemcheckbox", { name: "Not stated" }),
    );
    await user.keyboard("{Escape}");
    await user.click(
      within(sheet).getByRole("button", { name: "Website: All" }),
    );
    await user.click(
      await screen.findByRole("menuitemcheckbox", {
        name: /Anywhere on the web/,
      }),
    );
    await user.keyboard("{Escape}");
    await user.selectOptions(
      within(sheet).getByLabelText("Published"),
      "In the last 7 days",
    );
    await user.click(
      within(sheet).getByLabelText("Keep postings without a date"),
    );
    await user.type(within(sheet).getByLabelText("At least"), "50000{Enter}");
    await user.click(
      within(sheet).getByRole("button", { name: "Application: All" }),
    );
    await user.click(
      await screen.findByRole("menuitemcheckbox", { name: "Waiting for you" }),
    );
    await user.keyboard("{Escape}");
    expect(app.location()).toBe(
      "/jobs?work_mode=not_stated&source=web&published_within=7&published_undated=true&pay_min=50000&pay_period=year&application=waiting",
    );
  });

  it("applies the two presets", async () => {
    setUp();
    const { app, user, sheet } = await openSheet();
    await user.click(
      within(sheet).getByRole("button", { name: "Hide skipped and closed" }),
    );
    expect(app.location()).toBe(
      "/jobs?status=found&status=shortlisted&status=cv_ready&status=staged&status=applied&status=interview&status=rejected",
    );
    await user.click(
      within(sheet).getByRole("button", { name: "Found today" }),
    );
    expect(app.location()).toMatch(
      /&found_from=(\d{4}-\d{2}-\d{2})&found_to=\1$/,
    );
  });

  // Jobs J30: every posting by default; the Criteria entry keeps those within or outside.
  it("filters on the criteria of the search, as a chip, counted by Show, with the badge on the row", async () => {
    fakeSession(admin);
    const api = fakeJobsApi([
      makeJob(1),
      makeJob(2, {
        contract_type: "Freelance",
        work_mode: "remote",
        outside_criteria: ["contract", "work_mode"],
      }),
      makeJob(3),
    ]);
    const { app, user, sheet } = await openSheet();
    expect(api.requests.at(-1)?.has("criteria")).toBe(false);
    expect(
      within(sheet).getByRole("button", { name: "Show 3 postings" }),
    ).toBeInTheDocument();
    const criteria = within(sheet).getByLabelText("Criteria");
    expect(criteria).toHaveDisplayValue("All postings");
    await user.selectOptions(criteria, "Outside your criteria");
    expect(app.location()).toBe("/jobs?criteria=outside");
    expect(api.requests.at(-1)?.get("criteria")).toBe("outside");
    await user.click(
      await within(sheet).findByRole("button", { name: "Show 1 posting" }),
    );
    const chips = await screen.findByRole("list", { name: "Active filters" });
    expect(
      within(chips).getByText("Outside your criteria"),
    ).toBeInTheDocument();
    const list = screen.getByRole("list", { name: "Postings" });
    expect(
      await within(list).findByText(
        "Outside your criteria: freelance mission, remote",
      ),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^Filters/ }));
    await user.selectOptions(
      await screen.findByLabelText("Criteria"),
      "Within your criteria",
    );
    expect(app.location()).toBe("/jobs?criteria=within");
    expect(
      await screen.findByRole("button", { name: "Show 2 postings" }),
    ).toBeInTheDocument();
  });

  it("shows the postings of one search from the toast's address", async () => {
    const { api } = setUp();
    renderApp(`/jobs?sweep=${SWEEP}`);
    const chips = await screen.findByRole("list", { name: "Active filters" });
    expect(within(chips).getByText("Added by one search")).toBeInTheDocument();
    expect(api.requests.at(-1)?.get("sweep")).toBe(SWEEP);
  });
});

describe("the Search entry of the Filters panel (jobs J33b)", () => {
  const REMOTE = "00000000-0000-4000-8000-000000000001";

  it("is offered only once the person has a saved search", async () => {
    setUp();
    const { sheet } = await openSheet();
    expect(within(sheet).queryByLabelText("Search")).toBeNull();
  });

  it("keeps one search's postings in the address, as a chip named after it, through a reload", async () => {
    const { api, facets } = setUp();
    fakeSearches({ saved: [makeSearch(REMOTE, "Remote roles")] });
    const { app, user, sheet } = await openSheet();
    const search = await within(sheet).findByLabelText("Search");
    expect(search).toHaveDisplayValue("Every search");
    await user.selectOptions(search, "Remote roles");
    expect(app.location()).toBe(`/jobs?search=${REMOTE}`);
    expect(api.requests.at(-1)?.get("search")).toBe(REMOTE);
    await user.selectOptions(search, "Main search");
    expect(app.location()).toBe("/jobs?search=main");
    expect(facets.at(-1)?.get("search")).toBe("main");
    app.unmount();

    // A reload, or a shared link: the chip names the search.
    renderApp(`/jobs?search=${REMOTE}`);
    const chips = await screen.findByRole("list", { name: "Active filters" });
    expect(
      await within(chips).findByText("Added by Remote roles"),
    ).toBeInTheDocument();
    expect(api.requests.at(-1)?.get("search")).toBe(REMOTE);
  });

  it("says a search deleted meanwhile is gone, lets the chip go, and shows no error page", async () => {
    setUp();
    fakeSearches({ saved: [] });
    const app = renderApp(`/jobs?search=${REMOTE}`);
    const user = userEvent.setup();
    const chips = await screen.findByRole("list", { name: "Active filters" });
    expect(
      await within(chips).findByText("Added by a deleted search"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
    await user.click(await screen.findByRole("button", { name: /^Filters/ }));
    const sheet = await screen.findByRole("dialog", { name: "Filters" });
    expect(within(sheet).getByLabelText("Search")).toHaveDisplayValue(
      "A deleted search",
    );
    await user.keyboard("{Escape}");
    await user.click(
      screen.getByRole("button", {
        name: "Remove filter Added by a deleted search",
      }),
    );
    expect(app.location()).toBe("/jobs");
  });
});
