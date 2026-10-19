import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  connected,
  fakeScreens,
  FREE,
  ownLease,
  type Opened,
} from "@/test/browser";
import { renderWithQuery } from "@/test/render";
import { server } from "@/test/server";

import { BrowserCard } from "./browser-card";
import type { Browser } from "./browser-api";
import { ADDRESS_MAX } from "./signin-button";

const BAD_ADDRESS =
  "Type the address of the site, starting with https:// or http://.";
const SITE = "https://example.org/login?next=/a";
const SIGNING_IN: Browser = {
  ...FREE,
  lease: ownLease("signin"),
  holder: "you",
};

interface Calls {
  sessions: unknown[];
  headers: Headers[];
}

/** The api of the card: a session start answers `answer` (201 with the lease by default). */
function fakeApi(state: { browser: Browser }, answer?: () => Response): Calls {
  const calls: Calls = { sessions: [], headers: [] };
  server.use(
    http.get("*/api/browser", () => HttpResponse.json(state.browser)),
    http.post("*/api/browser/sessions", async ({ request }) => {
      calls.sessions.push(await request.json());
      calls.headers.push(request.headers);
      if (answer) return answer();
      state.browser = SIGNING_IN;
      return HttpResponse.json(
        { lease: SIGNING_IN.lease, queue: null },
        { status: 201 },
      );
    }),
  );
  return calls;
}

let opened: Opened[];

beforeEach(() => {
  opened = fakeScreens();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

async function openDialog(user: ReturnType<typeof userEvent.setup>) {
  await user.click(
    await screen.findByRole("button", { name: "Sign in to a site" }),
  );
  return screen.getByRole("dialog", { name: "Sign in to a site" });
}

describe("sign in to a site", () => {
  it("opens the typed address in the live view, the person at the keyboard", async () => {
    const calls = fakeApi({ browser: FREE });
    const user = userEvent.setup();
    renderWithQuery(<BrowserCard />);
    const dialog = await openDialog(user);
    const field = within(dialog).getByLabelText("Address of the site");
    expect(field).toHaveFocus();
    await user.type(field, SITE);
    await user.click(
      within(dialog).getByRole("button", { name: "Open the site" }),
    );

    expect(calls.sessions).toEqual([{ purpose: "signin", address: SITE }]);
    expect(calls.headers[0]?.get("X-Requested-With")).toBe("jobapp");
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
    const view = await connected(opened);
    expect(view.screen.viewOnly).toBe(false);
    expect(
      screen.getByText(
        "This is your own session: you have the keyboard and the mouse.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Take over" })).toBeNull();
    // While the session runs the card offers to close it, not to start another.
    expect(
      screen.queryByRole("button", { name: "Sign in to a site" }),
    ).toBeNull();
    expect(
      screen.getByRole("button", { name: "Close my browser" }),
    ).toBeInTheDocument();
  });

  it("submits with Enter", async () => {
    const calls = fakeApi({ browser: FREE });
    const user = userEvent.setup();
    renderWithQuery(<BrowserCard />);
    const dialog = await openDialog(user);
    await user.type(
      within(dialog).getByLabelText("Address of the site"),
      "http://example.org{Enter}",
    );
    expect(calls.sessions).toEqual([
      { purpose: "signin", address: "http://example.org" },
    ]);
  });

  it.each([
    "--remote-debugging-port=9222",
    "javascript:alert(1)",
    "example.org",
    "",
  ])(
    "shows the api's sentence for a refused address (%j) and starts nothing",
    async (typed) => {
      const state = { browser: FREE };
      const calls = fakeApi(state, () =>
        HttpResponse.json(
          { detail: BAD_ADDRESS, code: "bad_address" },
          { status: 422 },
        ),
      );
      const user = userEvent.setup();
      renderWithQuery(<BrowserCard />);
      const dialog = await openDialog(user);
      const field = within(dialog).getByLabelText("Address of the site");
      if (typed) await user.type(field, typed);
      await user.click(
        within(dialog).getByRole("button", { name: "Open the site" }),
      );

      const alert = await within(dialog).findByRole("alert");
      expect(alert).toHaveTextContent(BAD_ADDRESS);
      expect(calls.sessions).toEqual([{ purpose: "signin", address: typed }]);
      expect(field).toHaveAttribute("aria-invalid", "true");
      expect(field.getAttribute("aria-describedby")).toContain(alert.id);
      expect(field).toHaveFocus();
      expect(field).toHaveValue(typed);
      expect(screen.getByRole("dialog")).toBeInTheDocument();
      expect(opened).toHaveLength(0);
    },
  );

  it("clears the refusal when the dialog is closed and opened again", async () => {
    fakeApi({ browser: FREE }, () =>
      HttpResponse.json(
        { detail: BAD_ADDRESS, code: "bad_address" },
        { status: 422 },
      ),
    );
    const user = userEvent.setup();
    renderWithQuery(<BrowserCard />);
    let dialog = await openDialog(user);
    await user.type(within(dialog).getByLabelText("Address of the site"), "x");
    await user.click(
      within(dialog).getByRole("button", { name: "Open the site" }),
    );
    await within(dialog).findByRole("alert");
    await user.keyboard("{Escape}");
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
    dialog = await openDialog(user);
    expect(within(dialog).queryByRole("alert")).toBeNull();
    expect(within(dialog).getByLabelText("Address of the site")).toHaveValue(
      "",
    );
  });

  it("says so when the browser cannot be reached", async () => {
    fakeApi({ browser: FREE }, () =>
      HttpResponse.json(
        { detail: "The browser is not available right now." },
        { status: 503 },
      ),
    );
    const user = userEvent.setup();
    renderWithQuery(<BrowserCard />);
    const dialog = await openDialog(user);
    await user.type(within(dialog).getByLabelText("Address of the site"), SITE);
    await user.click(
      within(dialog).getByRole("button", { name: "Open the site" }),
    );
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "The browser is not available right now.",
    );
  });

  it("Cancel sends nothing", async () => {
    const calls = fakeApi({ browser: FREE });
    const user = userEvent.setup();
    renderWithQuery(<BrowserCard />);
    const dialog = await openDialog(user);
    await user.type(within(dialog).getByLabelText("Address of the site"), SITE);
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
    expect(calls.sessions).toEqual([]);
    expect(
      screen.getByRole("button", { name: "Sign in to a site" }),
    ).toHaveFocus();
  });

  it("waits in the queue and keeps its place with the same address", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const calls = fakeApi({ browser: { ...FREE, holder: "other" } }, () =>
      HttpResponse.json(
        {
          lease: null,
          queue: { purpose: "signin", position: 1, estimated_wait_s: 100 },
        },
        { status: 202 },
      ),
    );
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderWithQuery(<BrowserCard />);
    const dialog = await openDialog(user);
    await user.type(within(dialog).getByLabelText("Address of the site"), SITE);
    await user.click(
      within(dialog).getByRole("button", { name: "Open the site" }),
    );
    expect(
      await screen.findByText("Your browser starts in about 2 minutes."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Sign in to a site" }),
    ).toBeDisabled();
    await vi.advanceTimersByTimeAsync(20_000);
    await waitFor(() => {
      expect(calls.sessions).toHaveLength(2);
    });
    expect(calls.sessions[1]).toEqual({ purpose: "signin", address: SITE });
  });

  it("is off while the browser is not available", async () => {
    fakeApi({ browser: { ...FREE, available: false } });
    renderWithQuery(<BrowserCard />);
    await screen.findByText("The browser is not available right now.");
    expect(
      screen.getByRole("button", { name: "Sign in to a site" }),
    ).toBeDisabled();
  });

  it("takes an address as long as the api's cap, typed as an address", async () => {
    fakeApi({ browser: FREE });
    const user = userEvent.setup();
    renderWithQuery(<BrowserCard />);
    const dialog = await openDialog(user);
    const field = within(dialog).getByLabelText("Address of the site");
    expect(ADDRESS_MAX).toBe(2048);
    expect(field).toHaveAttribute("maxlength", "2048");
    expect(field).toHaveAttribute("type", "url");
    expect(field).toHaveAttribute("inputmode", "url");
  });

  it("has no dashes in its copy", async () => {
    fakeApi({ browser: FREE }, () =>
      HttpResponse.json(
        { detail: BAD_ADDRESS, code: "bad_address" },
        { status: 422 },
      ),
    );
    const user = userEvent.setup();
    renderWithQuery(<BrowserCard />);
    const dialog = await openDialog(user);
    await user.click(
      within(dialog).getByRole("button", { name: "Open the site" }),
    );
    await within(dialog).findByRole("alert");
    // The hint names the xn-- form of a host: a prefix, not a separator.
    const copy = dialog.textContent.replaceAll("xn--", "");
    expect(copy).not.toMatch(/[–—]| - |--/);
  });
});
