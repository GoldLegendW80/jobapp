import { MemoryRouter } from "react-router";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithQuery } from "@/test/render";
import { server } from "@/test/server";

import { BrowserCard, PROFILE_TITLE, TITLE } from "../components/browser-card";
import { type Browser, waitText } from "@/features/browser/api/browser-api";
import { screenFactory, type Screen } from "@/features/browser/helpers/screen";

// An assistant's lease of the person: the live view is view only until Take over.
const LEASE = {
  id: "0b6f7c1e-4a43-4f0e-9a51-1f2f3c4d5e6f",
  purpose: "apply_fill",
  automation: true,
  state: "active" as const,
  wait_reason: null,
  wait_host: null,
  wait_deadline: null,
  started_at: "2026-10-14T10:00:00Z",
  active_s: 3,
  max_s: 1800,
};
const FREE: Browser = {
  available: true,
  profile: { exists: true, size_bytes: 0, last_used_at: null },
  lease: null,
  holder: null,
  queue: [],
};
const MINE: Browser = { ...FREE, lease: LEASE, holder: "you" };
// A website's Sign in (browser B17): the person's own session, the person's input throughout.
const SIGNIN = { ...LEASE, purpose: "signin", automation: false };
const SIGNING_IN: Browser = { ...FREE, lease: SIGNIN, holder: "you" };
const TICKET = "t".repeat(43);

type Site = {
  host: string;
  state: "signed_in" | "expired";
  signed_in_at: string;
};
// Two job websites of invented hosts: one signed in, one whose login a search found ended.
const TWO_SITES: Site[] = [
  {
    host: "members.example.test",
    state: "signed_in",
    signed_in_at: "2026-11-03T09:00:00Z",
  },
  {
    host: "jobs.example.org",
    state: "expired",
    signed_in_at: "2026-10-20T09:00:00Z",
  },
];

interface Calls {
  tickets: unknown[];
  takeovers: unknown[];
  givebacks: unknown[];
  sessions: unknown[];
  deletes: number;
  headers: Headers[];
}

function fakeServer(state: {
  browser: Browser;
  session?: unknown;
  sites?: Site[];
}): Calls {
  const calls: Calls = {
    tickets: [],
    takeovers: [],
    givebacks: [],
    sessions: [],
    deletes: 0,
    headers: [],
  };
  server.use(
    http.get("*/api/browser", () => HttpResponse.json(state.browser)),
    http.get("*/api/browser/sites", () =>
      HttpResponse.json({ sites: state.sites ?? [] }),
    ),
    http.post("*/api/browser/sessions", async ({ request }) => {
      calls.sessions.push(await request.json());
      calls.headers.push(request.headers);
      if (state.session)
        return HttpResponse.json(state.session, { status: 202 });
      state.browser = SIGNING_IN;
      return HttpResponse.json({ lease: SIGNIN, queue: null }, { status: 201 });
    }),
    http.delete("*/api/browser/sessions/current", () => {
      calls.deletes += 1;
      state.browser = FREE;
      return new HttpResponse(null, { status: 204 });
    }),
    http.post("*/api/browser/live/ticket", async ({ request }) => {
      calls.tickets.push(await request.json());
      return HttpResponse.json(
        {
          ticket: TICKET,
          url: `/vnc/websockify?ticket=${TICKET}`,
          expires_in_s: 30,
        },
        { status: 201 },
      );
    }),
    http.post("*/api/browser/takeover", async ({ request }) => {
      calls.takeovers.push(await request.json());
      calls.headers.push(request.headers);
      return HttpResponse.json({ input: "you" });
    }),
    http.post("*/api/browser/giveback", async ({ request }) => {
      calls.givebacks.push(await request.json());
      return HttpResponse.json({ input: "assistant" });
    }),
  );
  return calls;
}

class FakeScreen implements Screen {
  viewOnly = true;
  disconnected = false;
  focused = 0;
  keys: number[] = [];
  disconnect() {
    this.disconnected = true;
  }
  focus() {
    this.focused += 1;
  }
  typeKey(keysym: number) {
    this.keys.push(keysym);
  }
  keysymOf(character: string) {
    return character.codePointAt(0) ?? 0;
  }
}

let opened: Array<{
  url: string;
  screen: FakeScreen;
  events: { connect(): void; refused(): void; disconnect(): void };
}>;

beforeEach(() => {
  opened = [];
  vi.spyOn(screenFactory, "open").mockImplementation((_target, url, events) => {
    const screen = new FakeScreen();
    opened.push({ url, screen, events });
    return Promise.resolve(screen);
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

function only<T>(items: T[]): T {
  const [item] = items;
  if (items.length !== 1 || item === undefined)
    throw new Error(`expected one item, got ${String(items.length)}`);
  return item;
}

async function connected() {
  await waitFor(() => {
    expect(opened).toHaveLength(1);
  });
  const view = only(opened);
  act(() => {
    view.events.connect();
  });
  return view;
}

const card = () => screen.getByRole("heading", { level: 2, name: TITLE });

describe("websites you are signed in to", () => {
  it("lists only the websites the browser is signed in to, each with its Sign in", async () => {
    fakeServer({ browser: FREE, sites: TWO_SITES });
    renderWithQuery(
      <MemoryRouter>
        <BrowserCard />
      </MemoryRouter>,
    );
    expect(card()).toBeInTheDocument();
    const list = await screen.findByRole("list", { name: TITLE });
    const lines = within(list).getAllByRole("listitem");
    expect(
      lines.map((line) => line.firstChild?.firstChild?.textContent),
    ).toEqual(["members.example.test", "jobs.example.org"]);
    const [kept, ended] = lines;
    if (!kept || !ended) throw new Error("two lines expected");
    expect(kept).toHaveTextContent("Since 3 Nov 2026");
    expect(kept).toHaveTextContent("Signed in");
    expect(
      within(kept).getByRole("button", {
        name: "Sign in to members.example.test",
      }),
    ).toHaveTextContent("Sign in");
    expect(
      within(ended).getByRole("button", {
        name: "Sign in to jobs.example.org",
      }),
    ).toHaveTextContent("Sign in again");
    expect(ended).toHaveTextContent("Your login on this website has ended.");
    // One Forget all logins for every website, never one per line.
    expect(
      screen.getAllByRole("button", { name: "Forget all logins" }),
    ).toHaveLength(1);
    expect(
      within(list).queryByRole("button", { name: "Forget all logins" }),
    ).toBeNull();
    // Nothing of the old card: no session of its own, no address to type, no LinkedIn switch.
    for (const gone of [
      "Look at my browser",
      "Sign in to a site",
      "Close my browser",
      "LinkedIn search",
    ])
      expect(screen.queryByRole("button", { name: gone })).toBeNull();
    expect(screen.queryByLabelText("Search words")).toBeNull();
    expect(screen.queryByText(/LinkedIn/)).toBeNull();
  });

  it("says where sign in happens when no website is signed in", async () => {
    fakeServer({ browser: FREE, sites: [] });
    renderWithQuery(
      <MemoryRouter>
        <BrowserCard />
      </MemoryRouter>,
    );
    expect(await screen.findByTestId("no-sites")).toHaveTextContent(
      "None yet. You sign in to a website from its line in Find jobs.",
    );
    expect(screen.getByRole("link", { name: "Find jobs" })).toHaveAttribute(
      "href",
      "/jobs?find=1",
    );
    expect(screen.queryByRole("list", { name: TITLE })).toBeNull();
    expect(
      screen.getByRole("button", { name: "Forget all logins" }),
    ).toBeInTheDocument();
  });

  it("shows the person's browsers at once, as an admin set it, read only", async () => {
    fakeServer({ browser: { ...FREE, browsers: 1 }, sites: [] });
    renderWithQuery(
      <MemoryRouter>
        <BrowserCard />
      </MemoryRouter>,
    );
    expect(await screen.findByTestId("browsers")).toHaveTextContent(
      "Your searches and applications use up to 1 browser at once. An admin sets this.",
    );
    expect(screen.queryByRole("radiogroup")).toBeNull();
  });

  it("says so when the websites cannot be read, and reads them again", async () => {
    const state = { browser: FREE, sites: TWO_SITES };
    fakeServer(state);
    let fail = true;
    server.use(
      http.get("*/api/browser/sites", () =>
        fail
          ? HttpResponse.json({ detail: "Try again later." }, { status: 503 })
          : HttpResponse.json({ sites: state.sites }),
      ),
    );
    const user = userEvent.setup();
    renderWithQuery(
      <MemoryRouter>
        <BrowserCard />
      </MemoryRouter>,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Your websites could not be read.",
    );
    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(
      await screen.findByRole("list", { name: TITLE }),
    ).toBeInTheDocument();
  });

  it("folds Complete my profile on a website, and opens it for an address a page sent", async () => {
    fakeServer({ browser: FREE });
    const user = userEvent.setup();
    const first = renderWithQuery(
      <MemoryRouter>
        <BrowserCard />
      </MemoryRouter>,
    );
    const fold = await screen.findByRole("button", { name: PROFILE_TITLE });
    expect(fold).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByLabelText("Site address")).toBeNull();
    await user.click(fold);
    expect(screen.getByLabelText("Site address")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Complete my profile on this site" }),
    ).toBeDisabled();
    first.unmount();

    renderWithQuery(
      <MemoryRouter
        initialEntries={[
          `/account?profile_site=${encodeURIComponent("https://portal.example.org/")}`,
        ]}
      >
        <BrowserCard />
      </MemoryRouter>,
    );
    expect(
      await screen.findByRole("button", { name: PROFILE_TITLE }),
    ).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByLabelText("Site address")).toHaveValue(
      "https://portal.example.org/",
    );
  });

  it("signs in again to a listed website in the person's own session, with the keyboard and the mouse", async () => {
    const calls = fakeServer({ browser: FREE, sites: TWO_SITES });
    const user = userEvent.setup();
    const { unmount } = renderWithQuery(
      <MemoryRouter>
        <BrowserCard />
      </MemoryRouter>,
    );
    await user.click(
      await screen.findByRole("button", {
        name: "Sign in to jobs.example.org",
      }),
    );
    expect(calls.sessions).toEqual([
      { purpose: "signin", address: "https://jobs.example.org/" },
    ]);
    expect(only(calls.headers).get("X-Requested-With")).toBe("jobapp");
    const view = await connected();
    expect(view.url).toBe(
      `ws://${window.location.host}/vnc/websockify?ticket=${TICKET}`,
    );
    expect(view.screen.viewOnly).toBe(false);
    // The session's one live view, in its line: the card does not show a second one.
    expect(opened).toHaveLength(1);
    expect(
      screen.getByText(
        "This is your own session: you have the keyboard and the mouse.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Use the browser myself" }),
    ).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Let the assistant continue" }),
    ).toBeNull();
    await user.click(screen.getByRole("button", { name: "Keyboard" }));
    await user.type(screen.getByLabelText("Type into your browser"), "a");
    expect(view.screen.keys).toEqual([0x61]);
    unmount();
    expect(calls.takeovers).toEqual([]);
    expect(calls.givebacks).toEqual([]);
  });

  it("an assistant's lease is view only until Take over", async () => {
    const calls = fakeServer({ browser: MINE });
    renderWithQuery(
      <MemoryRouter>
        <BrowserCard />
      </MemoryRouter>,
    );
    const view = await connected();
    expect(view.screen.viewOnly).toBe(true);
    expect(only(calls.tickets as Array<{ viewer: string }>).viewer).toMatch(
      /^[0-9a-f-]{36}$/,
    );
    expect(
      screen.getByText(
        "You are watching your browser. Choose Use the browser myself to type and click in it: the assistant waits.",
      ),
    ).toBeInTheDocument();
  });

  it.each(["captcha", "email_code", "forbidden"])(
    "opens a server-owned %s wait with live input",
    async (reason) => {
      const calls = fakeServer({
        browser: {
          ...MINE,
          lease: { ...LEASE, state: "waiting_person", wait_reason: reason },
        },
      });
      renderWithQuery(
        <MemoryRouter>
          <BrowserCard />
        </MemoryRouter>,
      );
      const view = await connected();
      expect(view.screen.viewOnly).toBe(false);
      expect(
        screen.getByRole("button", { name: "Keyboard" }),
      ).toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Use the browser myself" }),
      ).toBeNull();
      expect(calls.takeovers).toEqual([]);
    },
  );

  it("takes over with the same viewer id, types, and gives back", async () => {
    const calls = fakeServer({ browser: MINE });
    const user = userEvent.setup();
    renderWithQuery(
      <MemoryRouter>
        <BrowserCard />
      </MemoryRouter>,
    );
    const view = await connected();
    await user.click(
      screen.getByRole("button", { name: "Use the browser myself" }),
    );
    const ticket = only(calls.tickets as Array<{ viewer: string }>);
    expect(calls.takeovers).toEqual([{ viewer: ticket.viewer, slot: 0 }]);
    expect(view.screen.viewOnly).toBe(false);
    expect(view.screen.focused).toBe(1);
    expect(
      screen.getByText(
        "You have the keyboard and the mouse. Choose Let the assistant continue when you are done.",
      ),
    ).toBeInTheDocument();

    // The phone's keyboard: each character, then Enter, reaches the browser.
    await user.click(screen.getByRole("button", { name: "Keyboard" }));
    await user.type(
      screen.getByLabelText("Type into your browser"),
      "ok{Enter}",
    );
    expect(view.screen.keys).toEqual([0x6f, 0x6b, 0xff0d]);

    await user.click(
      screen.getByRole("button", { name: "Let the assistant continue" }),
    );
    expect(calls.givebacks).toEqual([{ viewer: ticket.viewer, slot: 0 }]);
    expect(view.screen.viewOnly).toBe(true);
    expect(
      screen.getByRole("button", { name: "Use the browser myself" }),
    ).toBeEnabled();
  });

  it("watches and takes over the second window of a lease alone in it", async () => {
    const second = { ...LEASE, slot: 1 };
    const calls = fakeServer({
      browser: { ...MINE, lease: second, slots: [second] },
    });
    const user = userEvent.setup();
    renderWithQuery(
      <MemoryRouter>
        <BrowserCard />
      </MemoryRouter>,
    );
    await connected();
    const ticket = only(
      calls.tickets as Array<{ viewer: string; slot: number }>,
    );
    expect(ticket.slot).toBe(1);
    await user.click(
      screen.getByRole("button", { name: "Use the browser myself" }),
    );
    expect(calls.takeovers).toEqual([{ viewer: ticket.viewer, slot: 1 }]);
  });

  it("sends no key while the assistant has the browser", async () => {
    fakeServer({ browser: MINE });
    const user = userEvent.setup();
    renderWithQuery(
      <MemoryRouter>
        <BrowserCard />
      </MemoryRouter>,
    );
    const view = await connected();
    await user.type(screen.getByLabelText("Type into your browser"), "x");
    expect(view.screen.keys).toEqual([]);
  });

  it("offers to reconnect when the live view closes, with a new ticket", async () => {
    const calls = fakeServer({ browser: MINE });
    const user = userEvent.setup();
    renderWithQuery(
      <MemoryRouter>
        <BrowserCard />
      </MemoryRouter>,
    );
    const view = await connected();
    act(() => {
      view.events.disconnect();
    });
    expect(screen.getByText("The live view is closed.")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Use the browser myself" }),
    ).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Reconnect" }));
    await waitFor(() => {
      expect(opened).toHaveLength(2);
    });
    expect(calls.tickets).toHaveLength(2);
  });

  it("says so when the server turns the view away, and reconnect clears it", async () => {
    fakeServer({ browser: MINE });
    const user = userEvent.setup();
    renderWithQuery(
      <MemoryRouter>
        <BrowserCard />
      </MemoryRouter>,
    );
    const view = await connected();
    act(() => {
      view.events.refused();
      view.events.disconnect();
    });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Too many open views of your browser. Close one.",
    );
    await user.click(screen.getByRole("button", { name: "Reconnect" }));
    await waitFor(() => {
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });
  });

  it("leaving the page while holding the input gives it back as this view", async () => {
    const calls = fakeServer({ browser: MINE });
    const user = userEvent.setup();
    const { unmount } = renderWithQuery(
      <MemoryRouter>
        <BrowserCard />
      </MemoryRouter>,
    );
    await connected();
    await user.click(
      screen.getByRole("button", { name: "Use the browser myself" }),
    );
    const ticket = only(calls.tickets as Array<{ viewer: string }>);
    unmount();
    await waitFor(() => {
      expect(calls.givebacks).toEqual([{ viewer: ticket.viewer, slot: 0 }]);
    });
  });

  it("leaving the page without the input gives nothing back", async () => {
    const calls = fakeServer({ browser: MINE });
    const { unmount } = renderWithQuery(
      <MemoryRouter>
        <BrowserCard />
      </MemoryRouter>,
    );
    await connected();
    unmount();
    expect(calls.givebacks).toEqual([]);
  });

  it("a view that opens after the page is gone is closed once, or not at all when it closed itself", async () => {
    fakeServer({ browser: MINE });
    for (const closedItself of [false, true]) {
      let resolve: (screen: Screen) => void = () => undefined;
      const pending: Array<{
        screen: FakeScreen;
        events: { disconnect(): void };
      }> = [];
      vi.spyOn(screenFactory, "open").mockImplementation(
        (_target, _url, events) => {
          pending.push({ screen: new FakeScreen(), events });
          return new Promise<Screen>((done) => {
            resolve = done;
          });
        },
      );
      const disconnect = vi.spyOn(FakeScreen.prototype, "disconnect");
      const { unmount } = renderWithQuery(
        <MemoryRouter>
          <BrowserCard />
        </MemoryRouter>,
      );
      await waitFor(() => {
        expect(pending).toHaveLength(1);
      });
      const view = only(pending);
      unmount();
      if (closedItself) view.events.disconnect();
      await act(async () => {
        resolve(view.screen);
        await Promise.resolve();
      });
      expect(disconnect).toHaveBeenCalledTimes(closedItself ? 0 : 1);
      vi.restoreAllMocks();
    }
  });

  it("says why when the ticket is refused", async () => {
    fakeServer({ browser: MINE });
    server.use(
      http.post("*/api/browser/live/ticket", () =>
        HttpResponse.json(
          { detail: "You are not using the browser right now." },
          { status: 403 },
        ),
      ),
    );
    renderWithQuery(
      <MemoryRouter>
        <BrowserCard />
      </MemoryRouter>,
    );
    expect(
      await screen.findByText("You are not using the browser right now."),
    ).toBeInTheDocument();
    expect(opened).toHaveLength(0);
  });

  it("says when the browser is not available", async () => {
    fakeServer({ browser: { ...FREE, available: false } });
    renderWithQuery(
      <MemoryRouter>
        <BrowserCard />
      </MemoryRouter>,
    );
    expect(
      await screen.findByText("The browser is not available right now."),
    ).toBeInTheDocument();
  });

  it("has no dashes in its copy", async () => {
    fakeServer({ browser: MINE, sites: TWO_SITES });
    const { container } = renderWithQuery(
      <MemoryRouter>
        <BrowserCard />
      </MemoryRouter>,
    );
    await connected();
    expect(container.textContent).not.toMatch(/[–—]| - /);
  });
});

describe("waitText", () => {
  it.each([
    [0, "Your browser starts in about a minute."],
    [60, "Your browser starts in about a minute."],
    [61, "Your browser starts in about 2 minutes."],
    [360, "Your browser starts in about 6 minutes."],
  ])("%i seconds", (seconds, text) => {
    expect(
      waitText({ purpose: "look", position: 1, estimated_wait_s: seconds }),
    ).toBe(text);
  });
});
