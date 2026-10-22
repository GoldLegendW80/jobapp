/**
 * Opportunities O5c (issue #497): the wizard's starter list of boards and sites in Search options.
 * Same component and routes as the wizard (onboarding/starter-boards.tsx); here it is closed until
 * opened (open when the search has nothing to sweep), waits while the tab has unsaved changes,
 * and reloads the forms after a choice so the next save neither loses the added boards nor
 * answers a conflict.
 */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { toast } from "sonner";
import { afterEach, describe, expect, it } from "vitest";

import type {
  StarterEntry,
  StarterList,
} from "@/features/onboarding/starter-api";
import { admin, fakeSession } from "@/test/auth";
import { fakeProfile, haraldLike } from "@/test/profile";
import { renderApp } from "@/test/render";
import { emptyStarter, server } from "@/test/server";

import type { Board, BrowserSource, Search } from "./api";
import { STARTER_BUSY, STARTER_HELD } from "./starter-card";

const DASH = /[‒–—―]| - /;

afterEach(() => {
  toast.dismiss();
});

function board(key: string, company: string): StarterEntry {
  const [ats, org] = key.split(":");
  return {
    key,
    kind: "board",
    company,
    ats: ats ?? null,
    org: org ?? null,
    source: null,
    query: null,
    location: null,
    why: "Artificial intelligence, France",
    added: false,
  };
}

const VIE: StarterEntry = {
  key: "source:vie",
  kind: "source",
  company: "Business France VIE",
  ats: null,
  org: null,
  source: "vie",
  query: "AI engineer",
  location: null,
  why: "Always proposed",
  added: false,
};

const LIST: StarterList = {
  ...emptyStarter,
  targets: true,
  entries: [
    board("ashby:fake-ok", "Example Robotics"),
    board("greenhouse:fake-ok2", "Sample Analytics"),
    VIE,
  ],
};

type State = ReturnType<typeof fakeProfile>;

/**
 * The starter routes as the api answers them: an add writes the search section (one revision),
 * any choice writes the preferences (one more), and the list follows.
 */
function fakeStarter(state: State, initial: StarterList = LIST) {
  let list = structuredClone(initial);
  const calls = { gets: 0, posts: [] as { add: string[]; remove: string[] }[] };
  server.use(
    http.get("*/api/opportunities/starter", () => {
      calls.gets += 1;
      return HttpResponse.json(list);
    }),
    http.post("*/api/opportunities/starter", async ({ request }) => {
      const body = (await request.json()) as {
        add: string[];
        remove: string[];
      };
      calls.posts.push(body);
      const added = list.entries.filter(
        (e) => body.add.includes(e.key) && !e.added,
      );
      const search = state.sections.search as Search;
      for (const entry of added) {
        if (entry.kind === "board")
          search.boards.push({
            company: entry.company,
            ats: entry.ats,
            org: entry.org,
            enabled: true,
            extra_exclude: { advanced_regex: false, terms: [] },
          } as Board);
        else
          (search.browser_sources ??= []).push({
            kind: entry.source,
            query: entry.query ?? "",
            location: entry.location,
            enabled: true,
          } as BrowserSource);
      }
      if (added.length) state.revision += 1;
      state.revision += 1;
      list = {
        ...list,
        entries: list.entries
          .filter((e) => !body.remove.includes(e.key))
          .map((e) =>
            added.some((a) => a.key === e.key) ? { ...e, added: true } : e,
          ),
      };
      return HttpResponse.json({ list, added: added.map((e) => e.key) });
    }),
  );
  return calls;
}

async function open(initial = haraldLike()) {
  fakeSession(admin);
  const state = fakeProfile(initial);
  const calls = fakeStarter(state);
  const user = userEvent.setup();
  renderApp("/profile?tab=search");
  await screen.findByRole("form", { name: "Search" });
  return { user, state, calls };
}

/** Holds every POST of a starter choice until `release` runs, as a slow link would. */
function holdPosts() {
  let release = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  server.use(
    http.post("*/api/opportunities/starter", async () => {
      await gate;
      // Nothing returned: the fake of fakeStarter answers.
    }),
  );
  return () => {
    release();
  };
}

const card = () =>
  screen
    .getByRole("heading", { name: "Suggested boards and sites" })
    .closest("section") as HTMLElement;
const searchForm = () => screen.getByRole("form", { name: "Search" });
const orgs = () =>
  within(searchForm())
    .getAllByLabelText(/^Org of board/)
    .map((input) => (input as HTMLInputElement).value);

describe("the starter list in Search options", () => {
  it("stays closed and unread while the search has boards, and opens on a click", async () => {
    const { user, calls } = await open();
    const toggle = within(card()).getByRole("button", {
      name: "Show the list",
    });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Example Robotics")).not.toBeInTheDocument();
    expect(calls.gets).toBe(0);

    await user.click(toggle);
    expect(await within(card()).findByText("Example Robotics")).toBeVisible();
    expect(
      within(card()).getByRole("button", { name: "Hide the list" }),
    ).toHaveAttribute("aria-expanded", "true");
    // Nothing ticked, nothing added before a click.
    for (const box of within(card()).getAllByRole("checkbox"))
      expect(box).not.toBeChecked();
    expect(calls.posts).toEqual([]);
    expect(card().textContent).not.toMatch(DASH);
  });

  it("is open at once when the search has no board and no site", async () => {
    const empty = haraldLike();
    empty.search = { ...empty.search, boards: [], browser_sources: [] };
    await open(empty);
    expect(await within(card()).findByText("Example Robotics")).toBeVisible();
    expect(within(card()).getByText(/under Company boards\.$/)).toBeVisible();
  });

  it("adds the ticked ones, shows them in the search, and the next save keeps them", async () => {
    const { user, state, calls } = await open();
    await user.click(
      within(card()).getByRole("button", { name: "Show the list" }),
    );
    await user.click(
      await within(card()).findByRole("checkbox", { name: /Example Robotics/ }),
    );
    await user.click(
      within(card()).getByRole("checkbox", { name: /Business France VIE/ }),
    );
    await user.click(
      within(card()).getByRole("button", { name: "Add the ticked ones" }),
    );
    expect(
      await within(card()).findByText(
        "Added 2 boards and sites. They are now under Company boards and Job sites in your browser.",
      ),
    ).toBeVisible();
    expect(calls.posts).toEqual([
      { add: ["ashby:fake-ok", "source:vie"], remove: [] },
    ]);
    // The forms read the profile again: the board is in the table, the list stays open.
    await waitFor(() => {
      expect(orgs()).toContain("fake-ok");
    });
    expect(within(card()).getAllByText("Added")).toHaveLength(2);

    // A save afterwards is based on the new revision: no conflict, the board kept.
    const before = state.revision;
    await user.clear(
      within(searchForm()).getByLabelText("Posting age window (days)"),
    );
    await user.type(
      within(searchForm()).getByLabelText("Posting age window (days)"),
      "14",
    );
    await user.click(
      within(searchForm()).getByRole("button", { name: "Save search" }),
    );
    await screen.findByText("Search saved.");
    const saved = state.saves.at(-1)!;
    expect(saved.body.base_revision).toBe(before);
    const data = saved.body.data as Search;
    expect(data.days).toBe(14);
    expect(data.boards.map((b) => b.org)).toContain("fake-ok");
    expect(data.browser_sources?.map((s) => s.kind)).toContain("vie");
  });

  it("takes an entry off the list, and a save after it does not conflict", async () => {
    const { user, state, calls } = await open();
    await user.click(
      within(card()).getByRole("button", { name: "Show the list" }),
    );
    await user.click(
      await within(card()).findByRole("button", {
        name: "Remove Sample Analytics from the list",
      }),
    );
    expect(
      await within(card()).findByText("Taken off the list."),
    ).toBeVisible();
    expect(
      within(card()).queryByText("Sample Analytics"),
    ).not.toBeInTheDocument();
    expect(calls.posts).toEqual([{ add: [], remove: ["greenhouse:fake-ok2"] }]);
    // The preferences moved one revision: the page reads the profile and the list again.
    await waitFor(() => {
      expect(calls.gets).toBe(2);
    });
    const before = state.revision;
    await user.clear(
      within(searchForm()).getByLabelText("Posting age window (days)"),
    );
    await user.type(
      within(searchForm()).getByLabelText("Posting age window (days)"),
      "21",
    );
    await user.click(
      within(searchForm()).getByRole("button", { name: "Save search" }),
    );
    await screen.findByText("Search saved.");
    expect(state.saves.at(-1)!.body.base_revision).toBe(before);
  });

  it("waits while the tab has unsaved changes, and opens again once saved", async () => {
    const { user, calls } = await open();
    await user.click(
      within(card()).getByRole("button", { name: "Show the list" }),
    );
    await within(card()).findByText("Example Robotics");
    await user.clear(
      within(searchForm()).getByLabelText("Posting age window (days)"),
    );
    await user.type(
      within(searchForm()).getByLabelText("Posting age window (days)"),
      "20",
    );
    expect(await within(card()).findByText(STARTER_HELD)).toBeVisible();
    expect(
      within(card()).getByRole("button", { name: "Add all" }),
    ).toBeDisabled();
    expect(
      within(card()).getByRole("button", {
        name: "Remove Example Robotics from the list",
      }),
    ).toBeDisabled();
    for (const box of within(card()).getAllByRole("checkbox"))
      expect(box).toBeDisabled();

    const gets = calls.gets;
    await user.click(
      within(searchForm()).getByRole("button", { name: "Save search" }),
    );
    await screen.findByText("Search saved.");
    await waitFor(() => {
      expect(
        within(card()).getByRole("button", { name: "Add all" }),
      ).toBeEnabled();
    });
    expect(within(card()).queryByText(STARTER_HELD)).not.toBeInTheDocument();
    // The saved search may name other targets: the list is read again.
    await waitFor(() => {
      expect(calls.gets).toBeGreaterThan(gets);
    });
    expect(calls.posts).toEqual([]);
  });

  it("keeps the Search form read only from the click until it reloads, so no typing is lost", async () => {
    const { user, state } = await open();
    const release = holdPosts();
    const days = () =>
      within(searchForm()).getByLabelText("Posting age window (days)");
    await user.click(
      within(card()).getByRole("button", { name: "Show the list" }),
    );
    await user.click(
      await within(card()).findByRole("checkbox", { name: /Example Robotics/ }),
    );
    await user.click(
      within(card()).getByRole("button", { name: "Add the ticked ones" }),
    );
    // The POST is held: the forms wait, and say why.
    expect(await screen.findByText(STARTER_BUSY)).toBeVisible();
    expect(screen.getByText(STARTER_BUSY)).toHaveAttribute("role", "status");
    expect(days()).toBeDisabled();
    expect(
      within(searchForm()).getByRole("button", { name: "Save search" }),
    ).toBeDisabled();
    // Typing in that window does nothing, so nothing can be dropped by the reload.
    await user.type(days(), "3");
    expect(days()).toHaveValue("30");

    release();
    await waitFor(() => {
      expect(orgs()).toContain("fake-ok");
    });
    await waitFor(() => {
      expect(days()).toBeEnabled();
    });
    expect(screen.queryByText(STARTER_BUSY)).not.toBeInTheDocument();
    // What is typed now stays, and its save is based on the new revision.
    await user.clear(days());
    await user.type(days(), "33");
    expect(days()).toHaveValue("33");
    const before = state.revision;
    await user.click(
      within(searchForm()).getByRole("button", { name: "Save search" }),
    );
    await screen.findByText("Search saved.");
    const saved = state.saves.at(-1)!;
    expect(saved.body.base_revision).toBe(before);
    expect((saved.body.data as Search).days).toBe(33);
  });

  it("keeps the Preferences card read only from the click until it reloads", async () => {
    const { user } = await open();
    const release = holdPosts();
    const preferences = () => screen.getByRole("form", { name: "Preferences" });
    const tone = () =>
      within(preferences()).getByLabelText("Tone of documents");
    await user.click(
      within(card()).getByRole("button", { name: "Show the list" }),
    );
    await user.click(
      await within(card()).findByRole("button", { name: "Add all" }),
    );
    expect(await screen.findByText(STARTER_BUSY)).toBeVisible();
    expect(tone()).toBeDisabled();
    expect(
      within(preferences()).getByLabelText("Language of documents"),
    ).toBeDisabled();

    release();
    await waitFor(() => {
      expect(orgs()).toContain("fake-ok2");
    });
    await waitFor(() => {
      expect(tone()).toBeEnabled();
    });
    const other = Array.from((tone() as HTMLSelectElement).options).find(
      (option) => !option.selected,
    )!;
    await user.selectOptions(tone(), other.value);
    expect(tone()).toHaveValue(other.value);
  });

  it("frees the forms when a choice adds no revision, and a later revision keeps what is typed", async () => {
    const { user } = await open();
    // A remove the api had already kept: the list answers, the profile does not move.
    server.use(
      http.post("*/api/opportunities/starter", () =>
        HttpResponse.json({ list: LIST, added: [] }),
      ),
    );
    await user.click(
      within(card()).getByRole("button", { name: "Show the list" }),
    );
    await user.click(
      await within(card()).findByRole("button", {
        name: "Remove Sample Analytics from the list",
      }),
    );
    expect(
      await within(card()).findByText("Taken off the list."),
    ).toBeVisible();
    const days = () =>
      within(searchForm()).getByLabelText("Posting age window (days)");
    await waitFor(() => {
      expect(days()).toBeEnabled();
    });
    await user.clear(days());
    await user.type(days(), "44");
    // A later, unrelated revision (a save of the Preferences card) does not remount the forms.
    const preferences = screen.getByRole("form", { name: "Preferences" });
    const tone = within(preferences).getByLabelText("Tone of documents");
    const other = Array.from((tone as HTMLSelectElement).options).find(
      (option) => !option.selected,
    )!;
    await user.selectOptions(tone, other.value);
    await user.click(
      within(preferences).getByRole("button", { name: "Save preferences" }),
    );
    await screen.findByText("Preferences saved.");
    expect(days()).toHaveValue("44");
    expect(
      within(searchForm()).getByRole("button", { name: "Save search" }),
    ).toBeEnabled();
  });

  it("says what to do when the saved search no longer reads", async () => {
    const { user } = await open();
    server.use(
      http.get("*/api/opportunities/starter", () =>
        HttpResponse.json(
          { detail: "unreadable", code: "search_unreadable" },
          { status: 409 },
        ),
      ),
    );
    await user.click(
      within(card()).getByRole("button", { name: "Show the list" }),
    );
    expect(
      await within(card()).findByText(
        /Your saved search no longer passes the checks\. Save it again, then try again\./,
      ),
    ).toBeVisible();
  });

  it("names the words it knows nothing of and points to Company boards", async () => {
    fakeSession(admin);
    const empty = haraldLike();
    empty.search = { ...empty.search, boards: [], browser_sources: [] };
    const state = fakeProfile(empty);
    fakeStarter(state, {
      ...emptyStarter,
      targets: true,
      unmatched: ["basket weaving"],
    });
    renderApp("/profile?tab=search");
    expect(
      await screen.findByText(
        "No starter board matches your search targets. None is listed for basket weaving.",
      ),
    ).toBeVisible();
    expect(card().textContent).toContain(
      "You can add any other company board by its address under Company boards.",
    );
    expect(card().textContent).not.toMatch(DASH);
  });
});
