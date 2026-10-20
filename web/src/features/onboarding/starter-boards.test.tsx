import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it } from "vitest";

import { profileKey } from "@/features/profile/api";
import { createQueryClient } from "@/lib/query";
import { emptyStarter, server } from "@/test/server";

import {
  entrySource,
  type StarterEntry,
  type StarterList,
} from "./starter-api";
import { StarterBoards } from "./starter-boards";

const DASH = /[‒–—―]| - /;

function board(
  key: string,
  company: string,
  extra: Partial<StarterEntry> = {},
): StarterEntry {
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
    ...extra,
  };
}

const GOOGLE: StarterEntry = {
  key: "source:google_careers",
  kind: "source",
  company: "Google",
  ats: null,
  org: null,
  source: "google_careers",
  query: "AI engineer",
  location: "Paris",
  why: "Artificial intelligence, France",
  added: false,
};

const LIST: StarterList = {
  ...emptyStarter,
  targets: true,
  entries: [
    board("ashby:fake-ok", "Example Robotics"),
    board("greenhouse:fake-ok2", "Sample Analytics", { why: "Data, France" }),
    GOOGLE,
  ],
};

/** The starter routes over a list held here, as the api answers them. */
function fakeStarter(initial: StarterList = LIST) {
  let list = structuredClone(initial);
  const posts: { add: string[]; remove: string[] }[] = [];
  server.use(
    http.get("*/api/opportunities/starter", () => HttpResponse.json(list)),
    http.post("*/api/opportunities/starter", async ({ request }) => {
      const body = (await request.json()) as {
        add: string[];
        remove: string[];
      };
      posts.push(body);
      const added = body.add.filter((key) =>
        list.entries.some((e) => e.key === key && !e.added),
      );
      list = {
        ...list,
        entries: list.entries
          .filter((e) => !body.remove.includes(e.key))
          .map((e) => (added.includes(e.key) ? { ...e, added: true } : e)),
      };
      return HttpResponse.json({ list, added });
    }),
  );
  return posts;
}

function renderPanel() {
  const client = createQueryClient();
  const router = createMemoryRouter(
    [
      { path: "/welcome", element: <StarterBoards /> },
      { path: "/profile", element: <p>Profile page</p> },
    ],
    { initialEntries: ["/welcome"] },
  );
  const utils = render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { client, router, ...utils };
}

const panel = () =>
  screen.findByRole("region", { name: "Boards and sites to search" });

describe("the starter list", () => {
  it("proposes the boards and sites with nothing ticked, and Add all adds every one", async () => {
    const posts = fakeStarter();
    const user = userEvent.setup();
    const { client } = renderPanel();
    const region = await panel();
    expect(await within(region).findByText("Example Robotics")).toBeVisible();
    expect(region).toHaveTextContent(
      "via Ashby. Artificial intelligence, France",
    );
    expect(region).toHaveTextContent(
      'Google Careers, searched for "AI engineer" in Paris',
    );
    expect(region).toHaveTextContent("Nothing is added until you choose.");
    for (const box of within(region).getAllByRole("checkbox"))
      expect(box).not.toBeChecked();
    expect(
      screen.getByRole("button", { name: "Add the ticked ones" }),
    ).toBeDisabled();
    expect(region.textContent).not.toMatch(DASH);
    client.setQueryData(profileKey, { revision: 1, sections: {} });

    await user.click(screen.getByRole("button", { name: "Add all" }));
    await waitFor(() => {
      expect(within(region).getAllByText("Added")).toHaveLength(3);
    });
    expect(posts).toEqual([
      {
        add: ["ashby:fake-ok", "greenhouse:fake-ok2", "source:google_careers"],
        remove: [],
      },
    ]);
    expect(within(region).queryAllByRole("checkbox")).toHaveLength(0);
    expect(within(region).queryByRole("button", { name: /Remove/ })).toBeNull();
    expect(screen.getByRole("button", { name: "Add all" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Added 3 boards and sites to Search options.",
    );
    // the summary's figures read the search section again
    expect(client.getQueryState(profileKey)?.isInvalidated).toBe(true);
  });

  it("adds only the ticked ones", async () => {
    const posts = fakeStarter();
    const user = userEvent.setup();
    renderPanel();
    const region = await panel();
    await user.click(
      await within(region).findByRole("checkbox", { name: /Sample Analytics/ }),
    );
    await user.click(
      screen.getByRole("button", { name: "Add the ticked ones" }),
    );
    await waitFor(() => {
      expect(within(region).getAllByText("Added")).toHaveLength(1);
    });
    expect(posts).toEqual([{ add: ["greenhouse:fake-ok2"], remove: [] }]);
    expect(screen.getByRole("status")).toHaveTextContent(
      "Added 1 board or site to Search options.",
    );
    expect(
      screen.getByRole("button", { name: "Add the ticked ones" }),
    ).toBeDisabled();
    // unticking before adding sends nothing
    const robotics = within(region).getByRole("checkbox", {
      name: /Example Robotics/,
    });
    await user.click(robotics);
    await user.click(robotics);
    expect(
      screen.getByRole("button", { name: "Add the ticked ones" }),
    ).toBeDisabled();
  });

  it("takes an entry off the list for good with Remove", async () => {
    const posts = fakeStarter();
    const user = userEvent.setup();
    renderPanel();
    const region = await panel();
    await user.click(
      await within(region).findByRole("button", {
        name: "Remove Example Robotics from the list",
      }),
    );
    await waitFor(() => {
      expect(within(region).queryByText("Example Robotics")).toBeNull();
    });
    expect(posts).toEqual([{ add: [], remove: ["ashby:fake-ok"] }]);
    expect(screen.getByRole("status")).toHaveTextContent("Taken off the list.");
  });

  it("shows a board already added as added", async () => {
    fakeStarter({
      ...LIST,
      entries: [board("ashby:fake-ok", "Example Robotics", { added: true })],
    });
    renderPanel();
    const region = await panel();
    expect(await within(region).findByText("Added")).toBeVisible();
    expect(within(region).queryByRole("checkbox")).toBeNull();
    expect(screen.getByRole("button", { name: "Add all" })).toBeDisabled();
  });

  it("says when the answers match nothing, and offers to add a board by its address", async () => {
    fakeStarter({
      ...emptyStarter,
      targets: true,
      unmatched: ["Nurse", "Agriculture"],
    });
    const user = userEvent.setup();
    const { router } = renderPanel();
    const region = await panel();
    expect(
      await within(region).findByText(
        "No starter board matches your answers. None is listed for Nurse, Agriculture.",
      ),
    ).toBeVisible();
    expect(region.textContent).not.toMatch(DASH);
    const link = within(region).getByRole("link", { name: "Search options" });
    expect(link).toHaveAttribute("href", "/profile?tab=search");
    await user.click(link);
    expect(router.state.location.search).toBe("?tab=search");
  });

  it("keeps the sources and still says when no board matches the answers", async () => {
    fakeStarter({
      ...emptyStarter,
      targets: true,
      unmatched: ["Nurse"],
      entries: [{ ...GOOGLE, query: "jobs", location: null }],
    });
    renderPanel();
    const region = await panel();
    expect(await within(region).findByText("Google")).toBeVisible();
    expect(
      within(region).getByText(
        "No starter board matches your answers. None is listed for Nurse.",
      ),
    ).toBeVisible();
    expect(
      within(region).getByRole("link", { name: "Search options" }),
    ).toBeVisible();
  });

  it("says so when there is nothing to propose without targets", async () => {
    fakeStarter(emptyStarter);
    renderPanel();
    const region = await panel();
    expect(
      await within(region).findByText("No starter board to propose yet."),
    ).toBeVisible();
  });

  it("explains a list that cannot load and retries", async () => {
    let calls = 0;
    server.use(
      http.get("*/api/opportunities/starter", () => {
        calls += 1;
        return calls === 1
          ? HttpResponse.json({ detail: "Down" }, { status: 503 })
          : HttpResponse.json(LIST);
      }),
    );
    const user = userEvent.setup();
    renderPanel();
    const alert = await screen.findByRole("alert", undefined, {
      timeout: 5000,
    });
    expect(alert).toHaveTextContent("The list of boards could not load.");
    await user.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Example Robotics")).toBeVisible();
  });

  it("asks to save Search options again when they no longer pass the checks", async () => {
    server.use(
      http.get("*/api/opportunities/starter", () =>
        HttpResponse.json(
          {
            detail: "Open Search options and save them again.",
            code: "search_unreadable",
          },
          { status: 409 },
        ),
      ),
    );
    renderPanel();
    expect(
      await screen.findByRole("alert", undefined, { timeout: 5000 }),
    ).toHaveTextContent(
      "Open Search options and save them again, then come back to this list.",
    );
  });

  it("keeps the list when a choice fails", async () => {
    fakeStarter();
    server.use(
      http.post("*/api/opportunities/starter", () =>
        HttpResponse.json(
          { detail: "Too many", code: "too_many" },
          { status: 409 },
        ),
      ),
    );
    const user = userEvent.setup();
    renderPanel();
    const region = await panel();
    await within(region).findByText("Example Robotics");
    await user.click(screen.getByRole("button", { name: "Add all" }));
    expect(await within(region).findByRole("alert")).toHaveTextContent(
      "That did not work.",
    );
    expect(within(region).getAllByRole("checkbox")).toHaveLength(3);
  });
});

describe("entrySource", () => {
  it.each([
    [board("ashby:a", "A"), "via Ashby"],
    [board("greenhouse:a", "A"), "via Greenhouse"],
    [board("lever:a", "A"), "via Lever"],
    [GOOGLE, 'Google Careers, searched for "AI engineer" in Paris'],
    [
      { ...GOOGLE, location: null },
      'Google Careers, searched for "AI engineer"',
    ],
    [
      {
        ...GOOGLE,
        source: "vie",
        location: null,
        company: "Business France VIE",
      },
      'Business France VIE, searched for "AI engineer"',
    ],
  ])("words %#", (entry, words) => {
    expect(entrySource(entry)).toBe(words);
    expect(words).not.toMatch(DASH);
  });
});
