import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { toast } from "sonner";
import { afterEach, describe, expect, it } from "vitest";

import { admin, fakeSession } from "@/test/auth";
import { fakeProfile, haraldLike } from "@/test/profile";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import type { Board, Search, Weekday } from "./api";

afterEach(() => {
  toast.dismiss();
});

async function openSearch(
  initial = haraldLike() as Parameters<typeof fakeProfile>[0],
) {
  fakeSession(admin);
  const state = fakeProfile(initial);
  const user = userEvent.setup();
  const app = renderApp("/profile?tab=search");
  await screen.findByRole("form", { name: "Search" });
  return { user, state, ...app };
}

interface CheckCall {
  ats: string;
  org: string;
}

/** The board check endpoint, answering `answer` (a body, or a full response). */
function fakeCheck(
  answer: Record<string, unknown> | (() => Response | Promise<Response>),
) {
  const calls: CheckCall[] = [];
  server.use(
    http.post("*/api/profile/boards/check", async ({ request }) => {
      calls.push((await request.json()) as CheckCall);
      if (typeof answer === "function") return answer();
      return HttpResponse.json(answer);
    }),
  );
  return calls;
}

const save = () => screen.getByRole("button", { name: "Save search" });
const saved = (state: ReturnType<typeof fakeProfile>, index = 0) =>
  state.saves[index]!.body.data as Search;
const status = () =>
  within(screen.getByRole("region", { name: "Add company" })).getByRole(
    "status",
  );
const rows = () =>
  within(screen.getByRole("table", { name: "Boards" }))
    .getAllByRole("row")
    .slice(1);
const companies = () =>
  rows().map(
    (row) =>
      within(row).queryByRole<HTMLInputElement>("textbox", {
        name: /^Company of board/,
      })?.value,
  );

async function addCompany(
  user: ReturnType<typeof userEvent.setup>,
  org: string,
  { company = "", ats = "ashby" } = {},
) {
  if (company)
    await user.type(screen.getByLabelText("New board company"), company);
  await user.selectOptions(screen.getByLabelText("New board ATS"), ats);
  await user.type(screen.getByLabelText("New board org"), org);
  await user.click(screen.getByRole("button", { name: "Check" }));
}

const manyBoards = (): Board[] =>
  Array.from({ length: 32 }, (_, i) => ({
    company: `Company ${String.fromCharCode(90 - (i % 26))}${i}`,
    ats: (["ashby", "greenhouse", "lever"] as const)[i % 3]!,
    org: `org-${i}`,
    enabled: i % 4 !== 0,
    extra_exclude: { advanced_regex: false, terms: [] },
  }));

describe("search tab: terms and preferences", () => {
  it("shows the terms, preferences, boards and rules", async () => {
    await openSearch();
    const include = screen.getByRole("list", {
      name: "Terms of Titles to include",
    });
    expect(include).toHaveTextContent("applied ai");
    expect(
      screen.getByRole("switch", {
        name: "Advanced (regular expressions) for Titles to include",
      }),
    ).toBeChecked();
    expect(
      screen.getByRole("list", { name: "Terms of Locations" }),
    ).toHaveTextContent("Paris");
    expect(
      screen.getByRole("button", { name: "CDI (permanent)" }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "VIE" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(screen.getByLabelText("Remote stance")).toHaveValue("any");
    expect(screen.getByLabelText("Minimum fit")).toHaveValue("70");
    expect(screen.getByLabelText("Posting age window (days)")).toHaveValue(
      "30",
    );
    expect(rows()).toHaveLength(2);
    expect(screen.getByLabelText("Org of board 1")).toHaveValue("mistral.ai");
    expect(
      screen.getByRole("switch", { name: "Sweep Datadog" }),
    ).not.toBeChecked();
    expect(screen.getByText("2 boards, 1 enabled")).toBeVisible();
    expect(screen.getByLabelText("Rule 1")).toHaveValue(
      "No cybersecurity roles.",
    );
  });

  it("edits terms, contract types, stance, fit and days, then saves", async () => {
    const { user, state } = await openSearch();
    await user.type(
      screen.getByLabelText("New term for Titles to exclude"),
      "\\bintern{enter}",
    );
    await user.type(
      screen.getByLabelText("New term for Locations"),
      "Lyon{enter}",
    );
    await user.click(screen.getByRole("button", { name: "Remove Remote" }));
    await user.click(screen.getByRole("button", { name: "VIE" }));
    await user.click(screen.getByRole("button", { name: "Freelance" }));
    await user.click(screen.getByRole("button", { name: "CDD (fixed term)" }));
    expect(screen.getByRole("button", { name: "VIE" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await user.selectOptions(screen.getByLabelText("Remote stance"), "hybrid");
    fireEvent.change(screen.getByLabelText("Minimum fit"), {
      target: { value: "82" },
    });
    expect(screen.getByText("82 / 100")).toBeVisible();
    const days = screen.getByLabelText("Posting age window (days)");
    await user.clear(days);
    await user.type(days, "14");
    await user.click(save());
    expect(await screen.findByText("Search saved.")).toBeVisible();
    const data = saved(state);
    expect(data.title_exclude).toEqual({
      advanced_regex: true,
      terms: ["\\bcyber\\b", "\\bintern"],
    });
    expect(data.location.terms).toEqual(["Paris", "Lyon"]);
    // Always in the canonical order, whatever the order of the clicks.
    expect(data.contract_types).toEqual(["CDI", "CDD", "VIE"]);
    expect(data.remote_stance).toBe("hybrid");
    expect(data.min_fit).toBe(82);
    expect(data.days).toBe(14);
    expect(data.boards).toEqual(haraldLike().search.boards);
  });

  it("refuses a days window outside 1 to 365 and accepts no stance", async () => {
    const { user, state } = await openSearch();
    const days = screen.getByLabelText("Posting age window (days)");
    for (const bad of ["0", "366", "7.5", "abc", " "]) {
      await user.clear(days);
      await user.type(days, bad);
      await user.click(save());
      expect(
        await screen.findByText("Enter a number of days between 1 and 365."),
      ).toBeVisible();
    }
    expect(state.saves).toHaveLength(0);
    await user.clear(days);
    await user.type(days, "365");
    await user.selectOptions(screen.getByLabelText("Remote stance"), "");
    await user.click(save());
    await screen.findByText("Search saved.");
    expect(saved(state).remote_stance).toBeNull();
    expect(saved(state).days).toBe(365);
  });

  it("puts the server's regex error under the right title chip", async () => {
    const { user } = await openSearch();
    server.use(
      http.put("*/api/profile/search", () =>
        HttpResponse.json(
          {
            detail:
              "data.title_include.terms: term 2: invalid regular expression at position 0: nothing to repeat",
          },
          { status: 422 },
        ),
      ),
    );
    await user.click(screen.getByRole("button", { name: "VIE" }));
    await user.click(save());
    const list = screen.getByRole("list", {
      name: "Terms of Titles to include",
    });
    const llm = within(list).getAllByRole("listitem")[1]!;
    expect(
      await within(llm).findByText(
        "Invalid regular expression at position 0: nothing to repeat.",
      ),
    ).toBeVisible();
  });

  it("adds, edits and removes never-apply rules", async () => {
    const { user, state } = await openSearch();
    await user.click(screen.getByRole("button", { name: "Add rule" }));
    await user.click(save());
    expect(await screen.findByText("Enter a rule.")).toBeVisible();
    await user.type(screen.getByLabelText("Rule 2"), "  No defence roles. ");
    await user.click(screen.getByRole("button", { name: "Remove rule 1" }));
    await user.click(save());
    await screen.findByText("Search saved.");
    expect(saved(state).never_apply).toEqual(["No defence roles."]);
  });

  it("maps the server's error on a rule to its field", async () => {
    const { user } = await openSearch();
    server.use(
      http.put("*/api/profile/search", () =>
        HttpResponse.json(
          { detail: "data.never_apply.0: control characters are not allowed" },
          { status: 422 },
        ),
      ),
    );
    await user.type(screen.getByLabelText("Rule 1"), "!");
    await user.click(save());
    expect(
      await screen.findByText("Control characters are not allowed."),
    ).toBeVisible();
    expect(screen.getByLabelText("Rule 1")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
  });

  it("starts empty for a new user", async () => {
    const { user, state } = await openSearch(null);
    expect(screen.getByText("No board yet.")).toBeVisible();
    expect(screen.getByText("No rule yet.")).toBeVisible();
    // Titles to include and exclude, locations, and the industries kept mounted under Advanced.
    expect(screen.getAllByText("No term yet.")).toHaveLength(4);
    expect(screen.getByLabelText("Minimum fit")).toHaveValue("70");
    await user.type(
      screen.getByLabelText("New term for Titles to include"),
      "ai engineer{enter}",
    );
    await user.click(save());
    await screen.findByText("Search saved.");
    expect(saved(state)).toEqual({
      title_include: { advanced_regex: false, terms: ["ai engineer"] },
      title_exclude: { advanced_regex: false, terms: [] },
      location: { advanced_regex: false, terms: [] },
      contract_types: [],
      remote_stance: null,
      min_fit: 70,
      days: 30,
      boards: [],
      never_apply: [],
      sweep_schedule: {
        enabled: false,
        days: [
          "monday",
          "tuesday",
          "wednesday",
          "thursday",
          "friday",
          "saturday",
          "sunday",
        ],
        times: ["06:17"],
        timezone: "Europe/Paris",
      },
      documents: { auto: true, min_fit: 70, daily_ceiling: 100 },
      seniority: [],
      industries: { advanced_regex: false, terms: [] },
      company_types: [],
      compensation: { salary: null, daily_rate: null },
    });
  });
});

describe("search tab: automatic search", () => {
  const line = () => screen.getByText("Automatic search:").parentElement;
  const ON = {
    enabled: true,
    days: ["monday", "friday"] as Weekday[],
    times: ["18:30", "06:17"],
    timezone: "America/New_York",
  };

  it("shows off for a section saved before the schedule existed, with a link to Jobs", async () => {
    const legacy = haraldLike();
    const search: Partial<Search> = { ...legacy.search };
    delete search.sweep_schedule;
    await openSearch({ ...legacy, search });
    expect(line()).toHaveTextContent("Automatic search: off");
    expect(
      screen.getByRole("link", { name: "Change in Jobs" }),
    ).toHaveAttribute("href", "/jobs");
  });

  it("describes a schedule that is on", async () => {
    const data = haraldLike();
    data.search.sweep_schedule = ON;
    await openSearch(data);
    expect(line()).toHaveTextContent(
      "Automatic search: on, Monday and Friday at 06:17 and 18:30 (America/New_York)",
    );
  });

  it("keeps the schedule unchanged when the tab is saved", async () => {
    const data = haraldLike();
    data.search.sweep_schedule = ON;
    const { user, state } = await openSearch(data);
    await user.clear(screen.getByLabelText("Posting age window (days)"));
    await user.type(screen.getByLabelText("Posting age window (days)"), "14");
    await user.click(save());
    await screen.findByText("Search saved.");
    expect(saved(state).sweep_schedule).toEqual(ON);
    expect(saved(state).days).toBe(14);
    // And again after the form was reset from the saved version.
    await user.click(screen.getByRole("button", { name: "CDD (fixed term)" }));
    await user.click(save());
    await waitFor(() => {
      expect(state.saves).toHaveLength(2);
    });
    expect(saved(state, 1).sweep_schedule).toEqual(ON);
  });

  it("saves the default off schedule for a section that had none", async () => {
    const legacy = haraldLike();
    const search: Partial<Search> = { ...legacy.search };
    delete search.sweep_schedule;
    const { user, state } = await openSearch({ ...legacy, search });
    await user.click(screen.getByRole("button", { name: "VIE" }));
    await user.click(save());
    await screen.findByText("Search saved.");
    expect(saved(state).sweep_schedule.enabled).toBe(false);
  });
});

describe("search tab: boards", () => {
  it("edits a board: company, ATS, org, switch, extra exclude terms, delete", async () => {
    const { user, state } = await openSearch();
    await user.click(screen.getByRole("switch", { name: "Sweep Datadog" }));
    await user.selectOptions(screen.getByLabelText("ATS of board 2"), "lever");
    const exclude = screen.getByRole("button", {
      name: "Exclude terms of Datadog",
    });
    expect(exclude).toHaveTextContent("None");
    expect(exclude).toHaveAttribute("aria-expanded", "false");
    await user.click(exclude);
    expect(exclude).toHaveAttribute("aria-expanded", "true");
    await user.type(
      screen.getByLabelText("New term for Exclude terms of Datadog"),
      "sales{enter}",
    );
    expect(exclude).toHaveTextContent("1 term");
    await user.click(screen.getByRole("button", { name: "Delete Mistral AI" }));
    expect(rows()).toHaveLength(2); // the board and its open exclude row
    await user.click(save());
    await screen.findByText("Search saved.");
    expect(saved(state).boards).toEqual([
      {
        company: "Datadog",
        ats: "lever",
        org: "datadog",
        enabled: true,
        extra_exclude: { advanced_regex: false, terms: ["sales"] },
      },
    ]);
  });

  it("refuses an invalid or duplicate org in the table before sending", async () => {
    const { user, state } = await openSearch();
    const org = screen.getByLabelText("Org of board 2");
    await user.clear(org);
    await user.type(org, "../etc");
    await user.click(save());
    expect(
      await screen.findByText(
        "Use letters, digits, dots, dashes and underscores, starting with a letter or digit.",
      ),
    ).toBeVisible();
    await user.clear(org);
    await user.type(org, "MISTRAL.AI");
    await user.selectOptions(screen.getByLabelText("ATS of board 2"), "ashby");
    await user.click(save());
    expect(
      await screen.findByText(
        "This board is already listed (same ATS and org).",
      ),
    ).toBeVisible();
    // The same org on another ATS is another board.
    await user.selectOptions(screen.getByLabelText("ATS of board 2"), "lever");
    await user.click(save());
    await screen.findByText("Search saved.");
    expect(state.saves).toHaveLength(1);
  });

  it("opens a board's exclude terms when the server refuses one of them", async () => {
    const { user } = await openSearch({
      ...haraldLike(),
      search: {
        ...haraldLike().search,
        boards: [
          {
            ...haraldLike().search.boards[0]!,
            extra_exclude: { advanced_regex: true, terms: ["sales"] },
          },
        ],
      },
    });
    server.use(
      http.put("*/api/profile/search", () =>
        HttpResponse.json(
          {
            detail:
              "data.boards.0.extra_exclude.terms: term 1: nested quantifiers such as (a+)+ are not allowed: they can make matching hang",
          },
          { status: 422 },
        ),
      ),
    );
    expect(
      screen.getByRole("button", { name: "Exclude terms of Mistral AI" }),
    ).toHaveAttribute("aria-expanded", "false");
    await user.click(screen.getByRole("button", { name: "VIE" }));
    await user.click(save());
    expect(
      await screen.findByText(
        "Nested quantifiers such as (a+)+ are not allowed: they can make matching hang.",
      ),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Exclude terms of Mistral AI" }),
    ).toHaveAttribute("aria-expanded", "true");
  });

  it("sorts by company both ways without changing the saved order", async () => {
    const boards = manyBoards();
    const { user, state } = await openSearch({
      ...haraldLike(),
      search: { ...haraldLike().search, boards },
    });
    expect(screen.getByText("32 boards, 24 enabled")).toBeVisible();
    const header = screen.getByRole("columnheader", { name: /Company/ });
    expect(header).toHaveAttribute("aria-sort", "none");
    await user.click(within(header).getByRole("button"));
    expect(header).toHaveAttribute("aria-sort", "ascending");
    const ascending = boards
      .map((b) => b.company)
      .sort((a, b) => a.localeCompare(b, "en", { sensitivity: "base" }));
    expect(companies()).toEqual(ascending);
    await user.click(within(header).getByRole("button"));
    expect(header).toHaveAttribute("aria-sort", "descending");
    expect(companies()).toEqual([...ascending].reverse());
    await user.click(within(header).getByRole("button"));
    expect(header).toHaveAttribute("aria-sort", "none");
    expect(companies()).toEqual(boards.map((b) => b.company));

    // Sorted, an edit still lands on the right board.
    await user.click(within(header).getByRole("button"));
    const first = boards.find((b) => b.company === ascending[0])!;
    const index = boards.indexOf(first) + 1;
    await user.click(
      screen.getByRole("switch", { name: `Sweep ${first.company}` }),
    );
    await user.click(save());
    await screen.findByText("Search saved.");
    expect(saved(state).boards.map((b) => b.company)).toEqual(
      boards.map((b) => b.company),
    );
    expect(saved(state).boards[index - 1]!.enabled).toBe(!first.enabled);
  });

  it("searches boards by company, org and ATS", async () => {
    const { user } = await openSearch({
      ...haraldLike(),
      search: { ...haraldLike().search, boards: manyBoards() },
    });
    const box = screen.getByRole("searchbox", { name: "Search boards" });
    await user.type(box, "org-1");
    // org-1 and org-10 to org-19
    expect(rows()).toHaveLength(11);
    expect(screen.getByText("11 of 32 boards shown")).toBeVisible();
    await user.clear(box);
    await user.type(box, "LEVER");
    expect(rows()).toHaveLength(10);
    await user.clear(box);
    await user.type(box, "nothing like this{enter}");
    expect(
      screen.getByText("No board matches “nothing like this”."),
    ).toBeVisible();
    // Enter in the box never submits.
    expect(screen.queryByText("Search saved.")).toBeNull();
  });
});

describe("search tab: add company with the board check", () => {
  it("checks a board, fills the company name, adds it and saves", async () => {
    const { user, state } = await openSearch();
    const calls = fakeCheck({
      exists: true,
      postings: 42,
      company_name: "Anthropic",
      error: null,
    });
    await addCompany(user, " anthropic ", { ats: "greenhouse" });
    expect(
      await within(status()).findByText(
        "Found on Greenhouse: Anthropic, 42 open postings.",
      ),
    ).toBeVisible();
    expect(calls).toEqual([{ ats: "greenhouse", org: "anthropic" }]);
    expect(screen.getByLabelText("New board company")).toHaveValue("Anthropic");
    await user.type(
      screen.getByRole("searchbox", { name: "Search boards" }),
      "zzz",
    );
    await user.click(screen.getByRole("button", { name: "Add board" }));
    // The search box clears so the new board shows.
    expect(
      screen.getByRole("searchbox", { name: "Search boards" }),
    ).toHaveValue("");
    expect(rows()).toHaveLength(3);
    expect(screen.getByLabelText("Org of board 3")).toHaveValue("anthropic");
    expect(
      screen.getByRole("switch", { name: "Sweep Anthropic" }),
    ).toBeChecked();
    expect(
      within(status()).getByText("Anthropic added. Save search to keep it."),
    ).toBeVisible();
    expect(screen.getByLabelText("New board org")).toHaveValue("");
    expect(screen.getByText("Unsaved changes")).toBeVisible();
    await user.click(save());
    await screen.findByText("Search saved.");
    expect(saved(state).boards[2]).toEqual({
      company: "Anthropic",
      ats: "greenhouse",
      org: "anthropic",
      enabled: true,
      extra_exclude: { advanced_regex: false, terms: [] },
    });
  });

  it("keeps a company typed by hand and counts one posting in the singular", async () => {
    const { user } = await openSearch();
    fakeCheck({
      exists: true,
      postings: 1,
      company_name: "Other Name",
      error: null,
    });
    await addCompany(user, "acme", { company: "Acme" });
    expect(
      await within(status()).findByText(
        "Found on Ashby: Other Name, 1 open posting.",
      ),
    ).toBeVisible();
    expect(screen.getByLabelText("New board company")).toHaveValue("Acme");
  });

  it("does not offer to add a board that does not exist", async () => {
    const { user } = await openSearch();
    fakeCheck({ exists: false, postings: null, error: "not_found" });
    await addCompany(user, "no-such-company", { company: "Nope" });
    expect(
      await within(status()).findByText(
        "No board named “no-such-company” on Ashby. Check the org as it appears in the careers page address.",
      ),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: /^Add (board|without a check)$/ }),
    ).toBeNull();
  });

  it.each([
    [
      "timeout",
      "Lever did not answer in time. Try again, or add the board without a check.",
    ],
    [
      "blocked",
      "Lever refused the check from the server. Add the board without a check if you are sure of the org.",
    ],
    [
      "upstream_error",
      "Lever answered with an error. Try again later, or add the board without a check.",
    ],
  ])(
    "on %s it explains and offers to add without a check",
    async (error, message) => {
      const { user } = await openSearch();
      fakeCheck({ exists: false, postings: null, error });
      await addCompany(user, "spotify", { company: "Spotify", ats: "lever" });
      expect(await within(status()).findByText(message)).toBeVisible();
      await user.click(
        screen.getByRole("button", { name: "Add without a check" }),
      );
      expect(screen.getByLabelText("Org of board 3")).toHaveValue("spotify");
      expect(screen.getByLabelText("ATS of board 3")).toHaveValue("lever");
    },
  );

  it("asks for the company name before adding", async () => {
    const { user } = await openSearch();
    fakeCheck({ exists: false, postings: null, error: "timeout" });
    await addCompany(user, "acme");
    await within(status()).findByText(/did not answer in time/);
    await user.click(
      screen.getByRole("button", { name: "Add without a check" }),
    );
    expect(within(status()).getByText("Enter the company name.")).toBeVisible();
    expect(rows()).toHaveLength(2);
  });

  it("forgets the result when the org or the ATS changes", async () => {
    const { user } = await openSearch();
    fakeCheck({ exists: true, postings: 3, error: null });
    await addCompany(user, "acme", { company: "Acme" });
    await within(status()).findByText("Found on Ashby: 3 open postings.");
    await user.type(screen.getByLabelText("New board org"), "x");
    expect(screen.queryByRole("button", { name: "Add board" })).toBeNull();
    await user.type(screen.getByLabelText("New board org"), "{backspace}");
    expect(screen.getByRole("button", { name: "Add board" })).toBeVisible();
    await user.selectOptions(screen.getByLabelText("New board ATS"), "lever");
    expect(screen.queryByRole("button", { name: "Add board" })).toBeNull();
  });

  it.each([
    [
      "../etc",
      "Use letters, digits, dots, dashes and underscores, starting with a letter or digit.",
    ],
    [
      "a..b",
      "Use letters, digits, dots, dashes and underscores, starting with a letter or digit.",
    ],
    [
      "acme corp",
      "Use letters, digits, dots, dashes and underscores, starting with a letter or digit.",
    ],
    ["x".repeat(101), "Use at most 100 characters."],
    ["MISTRAL.AI", "This board is already in your list."],
  ])("refuses %s before calling the api", async (org, message) => {
    const { user } = await openSearch();
    const calls = fakeCheck({ exists: true, postings: 1, error: null });
    await addCompany(user, org);
    expect(within(status()).getByText(message)).toBeVisible();
    expect(screen.getByLabelText("New board org")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    expect(calls).toEqual([]);
  });

  it("disables Check while the org is empty and while a check runs", async () => {
    const { user } = await openSearch();
    fakeCheck(async () => {
      await delay(50);
      return HttpResponse.json({ exists: true, postings: 0, error: null });
    });
    expect(screen.getByRole("button", { name: "Check" })).toBeDisabled();
    await user.type(screen.getByLabelText("New board org"), "quiet{enter}");
    expect(
      await screen.findByRole("button", { name: "Checking" }),
    ).toBeDisabled();
    expect(
      await within(status()).findByText("Found on Ashby: 0 open postings."),
    ).toBeVisible();
  });

  it("reports the rate limit with the wait", async () => {
    const { user } = await openSearch();
    fakeCheck(() =>
      HttpResponse.json(
        { detail: "Too many board checks. Wait a minute and try again." },
        { status: 429, headers: { "Retry-After": "42" } },
      ),
    );
    await addCompany(user, "acme");
    expect(
      await within(status()).findByText(
        "Too many checks. Try again in 42 seconds.",
      ),
    ).toBeVisible();
  });

  it("reports a refused request and a network failure", async () => {
    const { user } = await openSearch();
    fakeCheck(() =>
      HttpResponse.json(
        { detail: "org: String should match pattern" },
        { status: 422 },
      ),
    );
    await addCompany(user, "acme");
    expect(
      await within(status()).findByText(
        /^The check failed\. .*String should match pattern/,
      ),
    ).toBeVisible();
    fakeCheck(() => HttpResponse.error());
    await user.click(screen.getByRole("button", { name: "Check" }));
    expect(
      await within(status()).findByText(
        "The check failed. The API could not be reached.",
      ),
    ).toBeVisible();
  });

  it("ignores a malformed answer from the api", async () => {
    const { user } = await openSearch();
    fakeCheck({ exists: "yes" });
    await addCompany(user, "acme");
    expect(
      await within(status()).findByText(/^The check failed\./),
    ).toBeVisible();
    expect(screen.queryByRole("button", { name: "Add board" })).toBeNull();
  });

  it("keeps an added board through a conflict, then saves it", async () => {
    const { user, state } = await openSearch();
    fakeCheck({ exists: true, postings: 5, company_name: null, error: null });
    await addCompany(user, "acme", { company: "Acme" });
    await within(status()).findByText("Found on Ashby: 5 open postings.");
    await user.click(screen.getByRole("button", { name: "Add board" }));
    state.revision = 12;
    await user.click(save());
    const dialog = await screen.findByRole("alertdialog", {
      name: "This section changed elsewhere",
    });
    await user.click(
      within(dialog).getByRole("button", { name: "Keep editing" }),
    );
    expect(screen.getByLabelText("Org of board 3")).toHaveValue("acme");
    await user.click(save());
    await screen.findByText("Search saved.");
    await waitFor(() => {
      expect(state.saves.map((s) => s.body.base_revision)).toEqual([6, 12]);
    });
  });

  it("asks before leaving with an added board not saved", async () => {
    const { user } = await openSearch();
    fakeCheck({ exists: true, postings: 5, error: null });
    await addCompany(user, "acme", { company: "Acme" });
    await within(status()).findByText("Found on Ashby: 5 open postings.");
    await user.click(screen.getByRole("button", { name: "Add board" }));
    await user.click(screen.getByRole("tab", { name: /^Answers/ }));
    expect(
      await screen.findByRole("alertdialog", {
        name: "Discard unsaved changes?",
      }),
    ).toBeVisible();
  });
});
