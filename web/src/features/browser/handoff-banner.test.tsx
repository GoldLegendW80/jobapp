import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithQuery } from "@/test/render";
import { server } from "@/test/server";

import { BrowserCard } from "./browser-card";
import { type Browser } from "./browser-api";
import { HandoffBanner } from "./handoff-banner";
import { screenFactory, type Screen } from "./screen";
import { handoffOf } from "./use-handoff";

const ACTIVE = {
  id: "0b6f7c1e-4a43-4f0e-9a51-1f2f3c4d5e6f",
  purpose: "apply_fill",
  automation: true,
  state: "active" as const,
  wait_reason: null,
  wait_host: null,
  wait_deadline: null,
  wait_minutes_left: null,
  started_at: "2026-10-20T10:00:00Z",
  active_s: 40,
  max_s: 900,
};
const WAITING = {
  ...ACTIVE,
  state: "waiting_person" as const,
  wait_reason: "signin",
  wait_host: "jobs.example.org",
  wait_deadline: "2026-10-20T10:30:00Z",
  wait_minutes_left: 12,
};
const FREE: Browser = {
  available: true,
  profile: { exists: true, size_bytes: 0, last_used_at: null },
  lease: null,
  holder: null,
  queue: [],
};
const MINE: Browser = { ...FREE, lease: WAITING, holder: "you" };
const TICKET = "t".repeat(43);

type Calls = { done: number; cancel: number; tickets: number };

function fakeServer(
  state: { browser: Browser },
  answer: { status: number; detail?: string } = { status: 204 },
): Calls {
  const calls: Calls = { done: 0, cancel: 0, tickets: 0 };
  const reply = () =>
    answer.status === 204
      ? new HttpResponse(null, { status: 204 })
      : HttpResponse.json({ detail: answer.detail }, { status: answer.status });
  server.use(
    http.get("*/api/browser", () => HttpResponse.json(state.browser)),
    http.post("*/api/browser/handoff/done", () => {
      calls.done += 1;
      if (answer.status === 204)
        state.browser = { ...MINE, lease: { ...ACTIVE } };
      return reply();
    }),
    http.post("*/api/browser/handoff/cancel", () => {
      calls.cancel += 1;
      if (answer.status === 204) state.browser = FREE;
      return reply();
    }),
    http.post("*/api/browser/live/ticket", () => {
      calls.tickets += 1;
      return HttpResponse.json(
        {
          ticket: TICKET,
          url: `/vnc/websockify?ticket=${TICKET}`,
          expires_in_s: 30,
        },
        { status: 201 },
      );
    }),
  );
  return calls;
}

class FakeScreen implements Screen {
  viewOnly = true;
  disconnected = false;
  disconnect() {
    this.disconnected = true;
  }
  focus() {}
  typeKey() {}
  keysymOf(character: string) {
    return character.codePointAt(0) ?? 0;
  }
}

let views: Array<{ connect(): void }>;

beforeEach(() => {
  views = [];
  vi.spyOn(screenFactory, "open").mockImplementation((_t, _u, events) => {
    views.push(events);
    return Promise.resolve(new FakeScreen());
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("handoffOf", () => {
  it.each([
    ["no lease", FREE],
    ["an assistant's lease at work", { ...MINE, lease: ACTIVE }],
    [
      "the person's own session",
      { ...MINE, lease: { ...WAITING, automation: false } },
    ],
    ["another person's browser", { ...FREE, lease: null, holder: "other" }],
    [
      "a wait for something else",
      { ...MINE, lease: { ...WAITING, wait_reason: "captcha" } },
    ],
    [
      "a deadline passed",
      { ...MINE, lease: { ...WAITING, wait_minutes_left: 0 } },
    ],
  ] as Array<[string, Browser]>)("shows nothing for %s", (_, browser) => {
    expect(handoffOf(browser)).toBeNull();
  });

  it("names the host and the minutes left, and a blank host as the site", () => {
    expect(handoffOf(MINE)).toEqual({
      host: "jobs.example.org",
      minutesLeft: 12,
    });
    expect(
      handoffOf({ ...MINE, lease: { ...WAITING, wait_host: "  " } })?.host,
    ).toBe("the site");
  });
});

describe("HandoffBanner", () => {
  it("shows the site, the minutes left, and the three actions", async () => {
    fakeServer({ browser: MINE });
    renderWithQuery(<HandoffBanner />);

    const banner = await screen.findByRole("region", {
      name: "Sign in to jobs.example.org",
    });
    expect(banner).toHaveTextContent("12 minutes left.");
    expect(banner.textContent).not.toMatch(/\u2014|\u2013| - /);
    for (const name of ["Open the live view", "I'm signed in", "Cancel"])
      expect(within(banner).getByRole("button", { name })).toBeInTheDocument();
    expect(views).toHaveLength(0);
  });

  it("names the tab's host, never a site name the assistant was steered to", async () => {
    fakeServer({
      browser: {
        ...MINE,
        lease: {
          ...WAITING,
          wait_host: "pages",
          wait_site: "Google",
        } as Browser["lease"],
      },
    });
    renderWithQuery(<HandoffBanner />);

    const banner = await screen.findByRole("region", {
      name: "Sign in to pages",
    });
    expect(banner.textContent).not.toContain("Google");
  });

  it("says one minute in the singular", async () => {
    fakeServer({
      browser: { ...MINE, lease: { ...WAITING, wait_minutes_left: 1 } },
    });
    renderWithQuery(<HandoffBanner />);
    expect(
      await screen.findByRole("region", {
        name: "Sign in to jobs.example.org",
      }),
    ).toHaveTextContent("1 minute left.");
  });

  it("opens the live view with the person at the keyboard", async () => {
    const calls = fakeServer({ browser: MINE });
    const user = userEvent.setup();
    renderWithQuery(<HandoffBanner />);

    await user.click(
      await screen.findByRole("button", { name: "Open the live view" }),
    );
    await waitFor(() => {
      expect(views).toHaveLength(1);
    });
    views[0]?.connect();
    const live = await screen.findByRole("region", {
      name: "Live view of your browser",
    });
    expect(
      await within(live).findByText(/You have the keyboard and the mouse/),
    ).toBeInTheDocument();
    expect(
      within(live).queryByRole("button", { name: /Take over/ }),
    ).not.toBeInTheDocument();
    expect(calls.tickets).toBe(1);
    expect(
      screen.queryByRole("button", { name: "Open the live view" }),
    ).not.toBeInTheDocument();
  });

  it("I'm signed in is sent once, and the banner goes", async () => {
    const calls = fakeServer({ browser: MINE });
    const user = userEvent.setup();
    renderWithQuery(<HandoffBanner />);

    const done = await screen.findByRole("button", { name: "I'm signed in" });
    await user.dblClick(done);

    await waitFor(() => {
      expect(screen.queryByRole("region")).not.toBeInTheDocument();
    });
    expect(calls.done).toBe(1);
  });

  it("Cancel ends the hand off", async () => {
    const calls = fakeServer({ browser: MINE });
    const user = userEvent.setup();
    renderWithQuery(<HandoffBanner />);

    await user.click(await screen.findByRole("button", { name: "Cancel" }));

    await waitFor(() => {
      expect(screen.queryByRole("region")).not.toBeInTheDocument();
    });
    expect(calls).toMatchObject({ cancel: 1, done: 0 });
  });

  it("says why when the hand off is already over", async () => {
    fakeServer(
      { browser: MINE },
      { status: 404, detail: "No sign in is waiting for you." },
    );
    const user = userEvent.setup();
    renderWithQuery(<HandoffBanner />);

    await user.click(
      await screen.findByRole("button", { name: "I'm signed in" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "No sign in is waiting for you.",
    );
  });

  it("works from the keyboard alone", async () => {
    const calls = fakeServer({ browser: MINE });
    const user = userEvent.setup();
    renderWithQuery(<HandoffBanner />);
    await screen.findByRole("region", { name: "Sign in to jobs.example.org" });

    await user.tab();
    expect(
      screen.getByRole("button", { name: "Open the live view" }),
    ).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: "I'm signed in" })).toHaveFocus();
    await user.keyboard("{Enter}");

    await waitFor(() => {
      expect(calls.done).toBe(1);
    });
  });

  it("shows nothing to a person whose browser another person uses", async () => {
    fakeServer({ browser: { ...FREE, holder: "other" } });
    const { container } = renderWithQuery(<HandoffBanner />);
    await waitFor(() => {
      expect(container).toBeEmptyDOMElement();
    });
  });
});

describe("BrowserCard during a hand off", () => {
  it("shows the banner with its live view open, and one live view only", async () => {
    fakeServer({ browser: MINE });
    renderWithQuery(<BrowserCard />);

    const banner = await screen.findByRole("region", {
      name: "Sign in to jobs.example.org",
    });
    await waitFor(() => {
      expect(views).toHaveLength(1);
    });
    expect(
      within(banner).getByRole("region", { name: "Live view of your browser" }),
    ).toBeInTheDocument();
    expect(
      screen.getAllByRole("region", { name: "Live view of your browser" }),
    ).toHaveLength(1);
  });
});
