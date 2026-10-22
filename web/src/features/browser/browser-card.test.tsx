import { MemoryRouter } from "react-router";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithQuery } from "@/test/render";
import { server } from "@/test/server";

import { BrowserCard } from "./browser-card";
import { type Browser, waitText } from "./browser-api";
import { screenFactory, type Screen } from "./screen";

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
// "Look at my browser" (browser B7): the person's own session, the person's input throughout.
const LOOK = { ...LEASE, purpose: "look", automation: false };
const LOOKING: Browser = { ...FREE, lease: LOOK, holder: "you" };
const TICKET = "t".repeat(43);

interface Calls {
  tickets: unknown[];
  takeovers: unknown[];
  givebacks: unknown[];
  sessions: unknown[];
  deletes: number;
  headers: Headers[];
}

function fakeServer(state: { browser: Browser; session?: unknown }): Calls {
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
    http.post("*/api/browser/sessions", async ({ request }) => {
      calls.sessions.push(await request.json());
      calls.headers.push(request.headers);
      if (state.session)
        return HttpResponse.json(state.session, { status: 202 });
      state.browser = LOOKING;
      return HttpResponse.json({ lease: LOOK, queue: null }, { status: 201 });
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

describe("your browser card", () => {
  it("starts the person's own session with the keyboard and the mouse", async () => {
    const calls = fakeServer({ browser: FREE });
    const user = userEvent.setup();
    const { unmount } = renderWithQuery(
      <MemoryRouter>
        <BrowserCard />
      </MemoryRouter>,
    );
    await user.click(
      await screen.findByRole("button", { name: "Look at my browser" }),
    );
    expect(calls.sessions).toEqual([{ purpose: "look" }]);
    expect(only(calls.headers).get("X-Requested-With")).toBe("jobapp");
    const view = await connected();
    expect(view.url).toBe(
      `ws://${window.location.host}/vnc/websockify?ticket=${TICKET}`,
    );
    expect(view.screen.viewOnly).toBe(false);
    const asked = only(calls.tickets as Array<{ viewer: string }>);
    expect(asked.viewer).toMatch(/^[0-9a-f-]{36}$/);
    expect(
      screen.getByText(
        "This is your own session: you have the keyboard and the mouse.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Take over" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Give back" })).toBeNull();
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
        "You are watching your browser. Take over to use it yourself.",
      ),
    ).toBeInTheDocument();
  });

  it("takes over with the same viewer id, types, and gives back", async () => {
    const calls = fakeServer({ browser: MINE });
    const user = userEvent.setup();
    renderWithQuery(
      <MemoryRouter>
        <BrowserCard />
      </MemoryRouter>,
    );
    const view = await connected();
    await user.click(screen.getByRole("button", { name: "Take over" }));
    const ticket = only(calls.tickets as Array<{ viewer: string }>);
    expect(calls.takeovers).toEqual([{ viewer: ticket.viewer }]);
    expect(view.screen.viewOnly).toBe(false);
    expect(view.screen.focused).toBe(1);
    expect(
      screen.getByText(
        "You have the keyboard and the mouse. Give back when you are done.",
      ),
    ).toBeInTheDocument();

    // The phone's keyboard: each character, then Enter, reaches the browser.
    await user.click(screen.getByRole("button", { name: "Keyboard" }));
    await user.type(
      screen.getByLabelText("Type into your browser"),
      "ok{Enter}",
    );
    expect(view.screen.keys).toEqual([0x6f, 0x6b, 0xff0d]);

    await user.click(screen.getByRole("button", { name: "Give back" }));
    expect(calls.givebacks).toEqual([{ viewer: ticket.viewer }]);
    expect(view.screen.viewOnly).toBe(true);
    expect(screen.getByRole("button", { name: "Take over" })).toBeEnabled();
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
    expect(screen.getByRole("button", { name: "Take over" })).toBeDisabled();
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
    await user.click(screen.getByRole("button", { name: "Take over" }));
    const ticket = only(calls.tickets as Array<{ viewer: string }>);
    unmount();
    await waitFor(() => {
      expect(calls.givebacks).toEqual([{ viewer: ticket.viewer }]);
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

  it("closes the session", async () => {
    const calls = fakeServer({ browser: MINE });
    const user = userEvent.setup();
    renderWithQuery(
      <MemoryRouter>
        <BrowserCard />
      </MemoryRouter>,
    );
    const view = await connected();
    await user.click(screen.getByRole("button", { name: "Close my browser" }));
    expect(calls.deletes).toBe(1);
    await screen.findByRole("button", { name: "Look at my browser" });
    expect(view.screen.disconnected).toBe(true);
  });

  it("shows the place in the queue", async () => {
    fakeServer({
      browser: FREE,
      session: {
        lease: null,
        queue: { purpose: "look", position: 1, estimated_wait_s: 330 },
      },
    });
    const user = userEvent.setup();
    renderWithQuery(
      <MemoryRouter>
        <BrowserCard />
      </MemoryRouter>,
    );
    await user.click(
      await screen.findByRole("button", { name: "Look at my browser" }),
    );
    expect(
      await screen.findByText("Your browser starts in about 6 minutes."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Look at my browser" }),
    ).toBeDisabled();
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
    expect(
      screen.getByRole("button", { name: "Look at my browser" }),
    ).toBeDisabled();
  });

  it("has no dashes in its copy", async () => {
    fakeServer({ browser: MINE });
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
