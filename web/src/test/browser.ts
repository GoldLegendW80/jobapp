import { act, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, vi } from "vitest";

import type { Browser, Lease } from "@/features/browser/api/browser-api";
import { screenFactory, type Screen } from "@/features/browser/helpers/screen";

import { server } from "./server";

/** The browser of the browser module's tests: free, the person's profile present. */
export const FREE: Browser = {
  available: true,
  profile: { exists: true, size_bytes: 0, last_used_at: null },
  lease: null,
  holder: null,
  queue: [],
};

/** A session of the person's own (browser B7): no assistant, the person's input throughout. */
export function ownLease(purpose: "look" | "signin"): Lease {
  return {
    id: "6a1d2c3b-4e5f-4a6b-8c7d-9e0f1a2b3c4d",
    purpose,
    automation: false,
    state: "active",
    wait_reason: null,
    wait_host: null,
    wait_deadline: null,
    started_at: "2026-10-19T10:00:00Z",
    active_s: 1,
    max_s: 1800,
  };
}

export class FakeScreen implements Screen {
  viewOnly = true;
  disconnected = false;
  disconnects = 0;
  disconnect() {
    this.disconnected = true;
    this.disconnects += 1;
  }
  focus() {}
  typeKey() {}
  keysymOf(character: string) {
    return character.codePointAt(0) ?? 0;
  }
}

export interface Opened {
  screen: FakeScreen;
  events: { connect(): void; disconnect(): void };
}

/** Every live view the page opens, drawn by a fake noVNC screen; restored by vi.restoreAllMocks. */
export function fakeScreens(): Opened[] {
  const opened: Opened[] = [];
  vi.spyOn(screenFactory, "open").mockImplementation(
    (_target, _url, events) => {
      const screen = new FakeScreen();
      opened.push({ screen, events });
      return Promise.resolve(screen);
    },
  );
  server.use(
    http.post("*/api/browser/live/ticket", () =>
      HttpResponse.json(
        {
          ticket: "t".repeat(43),
          url: `/vnc/websockify?ticket=${"t".repeat(43)}`,
          expires_in_s: 30,
        },
        { status: 201 },
      ),
    ),
  );
  return opened;
}

/** The one live view opened, once its socket connected. */
export async function connected(opened: Opened[]): Promise<Opened> {
  await waitFor(() => {
    expect(opened).toHaveLength(1);
  });
  const [view] = opened;
  if (!view) throw new Error("no live view");
  act(() => {
    view.events.connect();
  });
  return view;
}

export interface OpenedWindow extends Opened {
  slot: number;
  target: HTMLElement;
}

/**
 * Like fakeScreens, with two browsers at once: each live view with the window (slot) its ticket
 * was asked for and the element it is drawn in, and every Take over with its slot.
 */
export function fakeWindows(): {
  opened: OpenedWindow[];
  takeovers: number[];
} {
  const opened: OpenedWindow[] = [];
  const takeovers: number[] = [];
  vi.spyOn(screenFactory, "open").mockImplementation((target, url, events) => {
    const screen = new FakeScreen();
    const ticket = new URL(url).searchParams.get("ticket") ?? "";
    opened.push({ screen, events, target, slot: Number(ticket.slice(-1)) });
    return Promise.resolve(screen);
  });
  server.use(
    http.post("*/api/browser/live/ticket", async ({ request }) => {
      const { slot } = (await request.json()) as { slot: number };
      const ticket = `${"t".repeat(42)}${String(slot)}`;
      return HttpResponse.json(
        { ticket, url: `/vnc/websockify?ticket=${ticket}`, expires_in_s: 30 },
        { status: 201 },
      );
    }),
    http.post("*/api/browser/takeover", async ({ request }) => {
      takeovers.push(((await request.json()) as { slot: number }).slot);
      return HttpResponse.json({ input: "you" });
    }),
  );
  return { opened, takeovers };
}

/** The window (slot) of the one live view drawn inside `region`. */
export function windowIn(opened: OpenedWindow[], region: HTMLElement): number {
  const found = opened.filter((view) => region.contains(view.target));
  expect(found).toHaveLength(1);
  return found[0]?.slot ?? -1;
}
