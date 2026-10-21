import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { toast } from "sonner";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { Feed, Search } from "@/features/profile/api";
import { admin, fakeSession } from "@/test/auth";
import { makeDetail } from "@/test/jobs";
import { fakeProfile, haraldLike } from "@/test/profile";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

const DASHES = /[‒–—―]|\s-\s/;

afterEach(() => {
  toast.dismiss();
});

beforeEach(() => {
  try {
    window.localStorage.clear();
  } catch {
    // no storage in this run: Advanced starts folded anyway
  }
});

function withFeeds(feeds: Feed[] | undefined) {
  const data = haraldLike();
  const search: Search = { ...data.search };
  if (feeds) search.feeds = feeds;
  else delete search.feeds;
  return { ...data, search };
}

async function openSearch(feeds?: Feed[]) {
  fakeSession(admin);
  const state = fakeProfile(withFeeds(feeds));
  const user = userEvent.setup();
  const app = renderApp("/profile?tab=search");
  await screen.findByRole("form", { name: "Search" });
  return { user, state, ...app };
}

const card = () =>
  screen.getByRole("heading", { name: "Freelance and project sources" })
    .parentElement!.parentElement!.parentElement!;
const save = () => screen.getByRole("button", { name: "Save search" });
const savedSearch = (state: ReturnType<typeof fakeProfile>, index = 0) =>
  state.saves[index]!.body.data as Search;
const off = (kind: Feed["kind"]): Feed => ({
  kind,
  terms: { advanced_regex: false, terms: [] },
  enabled: false,
});

describe("freelance and project sources card (O8)", () => {
  it("lists each source with its switch and attribution, all off by default", async () => {
    await openSearch();
    const sources = await within(card()).findByRole("list", {
      name: "Freelance sources",
    });
    const switches = within(sources).getAllByRole("switch");
    expect(switches.map((s) => s.getAttribute("aria-label"))).toEqual([
      "Read Remote OK",
      "Read We Work Remotely",
      "Read Hacker News: Freelancer? Seeking freelancer?",
      "Read Hacker News: Who is hiring?",
    ]);
    for (const s of switches) expect(s).not.toBeChecked();
    expect(
      within(sources).getByText(
        "Each posting names Remote OK as its source and links back to it.",
      ),
    ).toBeVisible();
    expect(
      within(card()).getByText(
        "No freelance source is read. Turn one on and it is read at the next search.",
      ),
    ).toBeVisible();
    // Terms are folded under Advanced until opened.
    const advanced = within(card()).getByRole("button", { name: "Advanced" });
    expect(advanced).toHaveAttribute("aria-expanded", "false");
    expect(
      within(card()).queryByText("Words for Remote OK"),
    ).not.toBeInTheDocument();
  });

  it("lists the platforms that need an opt in, with their reason and date, and no switch", async () => {
    await openSearch();
    const optIn = await within(card()).findByRole("region", {
      name: "Read only with your opt in",
    });
    const items = within(optIn).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    const malt = items[0]!;
    expect(malt).toHaveTextContent("Malt");
    expect(malt).toHaveTextContent(
      "No public mission board: clients contact freelancers in its own messaging.",
    );
    expect(malt).toHaveTextContent("Needs your opt in, coming later");
    expect(malt).toHaveTextContent("Checked on 20 Oct 2026.");
    expect(
      within(malt).getByRole("link", { name: /Terms of Malt/ }),
    ).toHaveAttribute("href", "https://www.malt.fr/legal");
    expect(within(optIn).queryByRole("switch")).not.toBeInTheDocument();
    expect(within(optIn).queryByRole("checkbox")).not.toBeInTheDocument();
    for (const item of items) expect(item.textContent).not.toMatch(DASHES);
  });

  it("turns one source on and saves it alone, through the Search save", async () => {
    const { user, state } = await openSearch();
    await user.click(
      await screen.findByRole("switch", { name: "Read We Work Remotely" }),
    );
    expect(
      within(card()).queryByText(/No freelance source is swept/),
    ).not.toBeInTheDocument();
    await user.click(save());
    await screen.findByText("Search saved.");
    expect(savedSearch(state).feeds).toEqual([
      {
        kind: "weworkremotely",
        terms: { advanced_regex: false, terms: [] },
        enabled: true,
      },
    ]);
    // Reloaded from the saved profile, it is still on.
    expect(
      screen.getByRole("switch", { name: "Read We Work Remotely" }),
    ).toBeChecked();
  });

  it("saves no feeds at all when none was touched", async () => {
    const { user, state } = await openSearch();
    await screen.findByRole("switch", { name: "Read Remote OK" });
    await user.click(screen.getByRole("button", { name: "Add rule" }));
    await user.type(screen.getByLabelText("Rule 2"), "No gambling roles.");
    await user.click(save());
    await screen.findByText("Search saved.");
    expect("feeds" in savedSearch(state)).toBe(false);
  });

  it("keeps saved feeds in their order, and turns one off without dropping it", async () => {
    const saved: Feed[] = [
      {
        kind: "hackernews_freelance",
        terms: { advanced_regex: false, terms: ["python"] },
        enabled: true,
      },
      off("remoteok"),
    ];
    const { user, state } = await openSearch(saved);
    const hn = await screen.findByRole("switch", {
      name: "Read Hacker News: Freelancer? Seeking freelancer?",
    });
    expect(hn).toBeChecked();
    await user.click(hn);
    await user.click(save());
    await screen.findByText("Search saved.");
    expect(savedSearch(state).feeds).toEqual([
      { ...saved[0]!, enabled: false },
      saved[1],
    ]);
  });

  it("edits a source's words inside Advanced", async () => {
    const { user, state } = await openSearch();
    await screen.findByRole("switch", { name: "Read Remote OK" });
    await user.click(within(card()).getByRole("button", { name: "Advanced" }));
    const words = within(card()).getByRole("region", { name: "Advanced" });
    const input = within(words).getAllByRole("textbox")[0]!;
    await user.type(input, "contract{Enter}");
    await user.click(save());
    await screen.findByText("Search saved.");
    expect(savedSearch(state).feeds).toEqual([
      {
        kind: "remoteok",
        terms: { advanced_regex: false, terms: ["contract"] },
        enabled: false,
      },
    ]);
  });

  it("opens Advanced on a refused word and keeps the save from going out", async () => {
    const { user, state } = await openSearch([
      {
        kind: "remoteok",
        terms: { advanced_regex: true, terms: ["(unclosed"] },
        enabled: true,
      },
    ]);
    await screen.findByRole("switch", { name: "Read Remote OK" });
    await user.click(save());
    await waitFor(() => {
      expect(
        within(card()).getByRole("button", { name: "Advanced" }),
      ).toHaveAttribute("aria-expanded", "true");
    });
    expect(state.saves).toHaveLength(0);
  });

  it("shows the conflict dialog on a stale revision and keeps the switch", async () => {
    const { user, state } = await openSearch();
    await user.click(
      await screen.findByRole("switch", { name: "Read Remote OK" }),
    );
    state.revision = 12;
    await user.click(save());
    const dialog = await screen.findByRole("alertdialog", {
      name: "This section changed elsewhere",
    });
    await user.click(
      within(dialog).getByRole("button", { name: "Keep editing" }),
    );
    expect(
      screen.getByRole("switch", { name: "Read Remote OK" }),
    ).toBeChecked();
    await user.click(save());
    await screen.findByText("Search saved.");
    expect(savedSearch(state, 1).feeds?.[0]).toMatchObject({
      kind: "remoteok",
      enabled: true,
    });
  });

  it("says so when the catalogue fails, and still shows the saved switches", async () => {
    server.use(
      http.get("*/api/opportunities/feeds", () =>
        HttpResponse.json({ detail: "down" }, { status: 503 }),
      ),
    );
    await openSearch([
      {
        kind: "weworkremotely",
        terms: { advanced_regex: false, terms: [] },
        enabled: true,
      },
    ]);
    expect(
      await within(card()).findByText(
        /The list of sources could not be loaded/,
      ),
    ).toHaveTextContent("The sources you saved still show.");
    const switches = within(card()).getAllByRole("switch");
    expect(switches.map((s) => s.getAttribute("aria-label"))).toEqual([
      "Read We Work Remotely",
    ]);
    expect(switches[0]).toBeChecked();
    expect(
      within(card()).queryByRole("region", {
        name: "Read only with your opt in",
      }),
    ).not.toBeInTheDocument();
  });

  it("adds a mission by hand with the contract Freelance preselected, without saving the search", async () => {
    const sent: unknown[] = [];
    server.use(
      http.post("*/api/jobs", async ({ request }) => {
        sent.push(await request.json());
        return HttpResponse.json(
          makeDetail({
            id: "aaaaaaaa-0000-4000-9000-000000000009",
            company: "Quill Example",
            title: "AI Engineer",
            contract_type: "Freelance",
            fit: null,
          }),
          { status: 201 },
        );
      }),
    );
    const { user, state, location } = await openSearch();
    await user.click(
      within(card()).getByRole("button", { name: "Add a mission by hand" }),
    );
    const dialog = await screen.findByRole("dialog", { name: "Add a posting" });
    expect(within(dialog).getByLabelText("Contract type")).toHaveValue(
      "Freelance",
    );
    await user.type(within(dialog).getByLabelText("Company"), "Quill Example");
    await user.type(within(dialog).getByLabelText("Title"), "AI Engineer");
    await user.click(
      within(dialog).getByRole("button", { name: "Add posting" }),
    );
    expect(
      await within(card()).findByRole("link", { name: "Find it in Discover" }),
    ).toHaveAttribute("href", "/discover");
    expect(sent).toEqual([
      {
        company: "Quill Example",
        title: "AI Engineer",
        location: "",
        contract_type: "Freelance",
        url: null,
        posting_text: null,
      },
    ]);
    expect(state.saves).toHaveLength(0);
    expect(location()).toBe("/profile?tab=search");
  });

  it("uses no dash as a separator in any text it shows", async () => {
    await openSearch();
    await within(card()).findByRole("list", { name: "Freelance sources" });
    expect(card().textContent).not.toMatch(DASHES);
  });
});
