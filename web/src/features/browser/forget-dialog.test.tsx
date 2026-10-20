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
import { sizeText, type Browser } from "./browser-api";
import { FORGET_ENDS, FORGET_LOST, FORGET_NEXT } from "./forget-dialog";

const USED: Browser = {
  ...FREE,
  profile: {
    exists: true,
    size_bytes: 34_200_000,
    last_used_at: "2026-10-19T09:00:00Z",
  },
};
const EMPTY: Browser = {
  ...FREE,
  profile: { exists: false, size_bytes: 0, last_used_at: null },
};
const FORGOTTEN =
  "Your logins are forgotten. Your browser starts empty next time.";

interface Calls {
  forgets: number;
  headers: Headers[];
}

/** The api of the card; DELETE /api/browser/profile answers `answer`, else forgets. */
function fakeApi(state: { browser: Browser }, answer?: () => Response): Calls {
  const calls: Calls = { forgets: 0, headers: [] };
  server.use(
    http.get("*/api/browser", () => HttpResponse.json(state.browser)),
    http.delete("*/api/browser/profile", ({ request }) => {
      calls.forgets += 1;
      calls.headers.push(request.headers);
      if (answer) return answer();
      state.browser = EMPTY;
      return new HttpResponse(null, { status: 204 });
    }),
  );
  return calls;
}

let opened: Opened[];

beforeEach(() => {
  opened = fakeScreens();
});

afterEach(() => {
  vi.restoreAllMocks();
});

async function ask(user: ReturnType<typeof userEvent.setup>) {
  await user.click(
    await screen.findByRole("button", { name: "Forget all logins" }),
  );
  return screen.getByRole("alertdialog", { name: "Forget all logins?" });
}

describe("forget all logins", () => {
  it("asks first and names what is lost, with the profile's size", async () => {
    const calls = fakeApi({ browser: USED });
    const user = userEvent.setup();
    renderWithQuery(<BrowserCard />);
    const dialog = await ask(user);
    expect(dialog).toHaveTextContent(`${FORGET_LOST} It holds about 34 MB.`);
    expect(dialog).toHaveTextContent(FORGET_ENDS);
    expect(dialog).toHaveTextContent(FORGET_NEXT);
    expect(calls.forgets).toBe(0);
  });

  it("names no size when there is no profile yet", async () => {
    fakeApi({ browser: EMPTY });
    const user = userEvent.setup();
    renderWithQuery(<BrowserCard />);
    const dialog = await ask(user);
    expect(dialog).toHaveTextContent(FORGET_LOST);
    expect(dialog).not.toHaveTextContent("It holds");
  });

  it("Keep my logins sends nothing", async () => {
    const calls = fakeApi({ browser: USED });
    const user = userEvent.setup();
    renderWithQuery(<BrowserCard />);
    const dialog = await ask(user);
    await user.click(
      within(dialog).getByRole("button", { name: "Keep my logins" }),
    );
    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).toBeNull();
    });
    expect(calls.forgets).toBe(0);
    expect(screen.queryByText(FORGOTTEN)).toBeNull();
  });

  it("forgets, closes the running session and says so", async () => {
    const calls = fakeApi({
      browser: { ...USED, lease: ownLease("signin"), holder: "you" },
    });
    const user = userEvent.setup();
    renderWithQuery(<BrowserCard />);
    const view = await connected(opened);
    const dialog = await ask(user);
    await user.click(
      within(dialog).getByRole("button", { name: "Forget all logins" }),
    );

    expect(calls.forgets).toBe(1);
    expect(calls.headers[0]?.get("X-Requested-With")).toBe("jobapp");
    expect(await screen.findByRole("status")).toHaveTextContent(FORGOTTEN);
    expect(screen.queryByRole("alertdialog")).toBeNull();
    await screen.findByRole("button", { name: "Look at my browser" });
    expect(view.screen.disconnected).toBe(true);
  });

  it("forgets twice without complaint", async () => {
    const calls = fakeApi({ browser: EMPTY });
    const user = userEvent.setup();
    renderWithQuery(<BrowserCard />);
    for (let round = 0; round < 2; round += 1) {
      const dialog = await ask(user);
      await user.click(
        within(dialog).getByRole("button", { name: "Forget all logins" }),
      );
      await waitFor(() => {
        expect(screen.queryByRole("alertdialog")).toBeNull();
      });
    }
    expect(calls.forgets).toBe(2);
    expect(screen.getByText(FORGOTTEN)).toBeInTheDocument();
  });

  it("shows a refusal in the dialog and keeps it open", async () => {
    fakeApi({ browser: USED }, () =>
      HttpResponse.json(
        { detail: "The browser is not available right now." },
        { status: 503 },
      ),
    );
    const user = userEvent.setup();
    renderWithQuery(<BrowserCard />);
    const dialog = await ask(user);
    await user.click(
      within(dialog).getByRole("button", { name: "Forget all logins" }),
    );
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "The browser is not available right now.",
    );
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    expect(screen.queryByText(FORGOTTEN)).toBeNull();
  });

  it("a new session clears the sentence", async () => {
    const state = { browser: USED };
    fakeApi(state);
    server.use(
      http.post("*/api/browser/sessions", () => {
        state.browser = { ...EMPTY, lease: ownLease("look"), holder: "you" };
        return HttpResponse.json(
          { lease: ownLease("look"), queue: null },
          { status: 201 },
        );
      }),
    );
    const user = userEvent.setup();
    renderWithQuery(<BrowserCard />);
    const dialog = await ask(user);
    await user.click(
      within(dialog).getByRole("button", { name: "Forget all logins" }),
    );
    await screen.findByText(FORGOTTEN);
    await user.click(
      screen.getByRole("button", { name: "Look at my browser" }),
    );
    await connected(opened);
    expect(screen.queryByText(FORGOTTEN)).toBeNull();
  });

  it("has no dashes in its copy", async () => {
    fakeApi({ browser: USED });
    const user = userEvent.setup();
    renderWithQuery(<BrowserCard />);
    const dialog = await ask(user);
    expect(dialog.textContent).not.toMatch(/[–—]| - /);
    expect(FORGOTTEN).not.toMatch(/[–—]| - /);
  });
});

describe("sizeText", () => {
  it.each([
    [0, "less than 1 MB"],
    [999_999, "less than 1 MB"],
    [1_000_000, "about 1 MB"],
    [34_200_000, "about 34 MB"],
    [999_400_000, "about 999 MB"],
    [999_600_000, "about 1.0 GB"],
    [1_000_000_000, "about 1.0 GB"],
    [1_200_000_000, "about 1.2 GB"],
  ])("%i bytes", (bytes, text) => {
    expect(sizeText(bytes)).toBe(text);
  });
});
