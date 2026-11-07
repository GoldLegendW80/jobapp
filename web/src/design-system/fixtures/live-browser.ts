import { http, HttpResponse } from "msw";

import type { Browser, Lease } from "@/features/browser/api/browser-api";
import { screenFactory, type Screen } from "@/features/browser/helpers/screen";
import { server } from "@/test/server";

/**
 * Stories of the live view: invented pages drawn where noVNC would draw the person's browser, the
 * leases GET /api/browser answers, and the ticket, take over and give back routes. No real site.
 */

const svg = (body: string) =>
  `data:image/svg+xml;charset=utf-8,${encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" width="1440" height="900" viewBox="0 0 1440 900"><rect width="1440" height="900" fill="#ffffff"/><rect width="1440" height="72" fill="#eef0f3"/><rect x="24" y="20" width="600" height="32" rx="16" fill="#ffffff" stroke="#d1d5db"/>${body}</svg>`,
  )}`;

const text = (
  x: number,
  y: number,
  size: number,
  words: string,
  weight = 400,
) =>
  `<text x="${String(x)}" y="${String(y)}" font-family="sans-serif" font-size="${String(size)}" font-weight="${String(weight)}" fill="#111827">${words}</text>`;

/** A job website's results, as the assistant searches it. */
const RESULTS = svg(
  [
    text(44, 43, 16, "careers.example.org/search?q=data+engineer"),
    text(64, 140, 34, "Data engineer jobs in Lyon", 600),
    ...[
      ["Data engineer", "Lumen Freight, Lyon"],
      ["Senior data engineer", "Example Robotics, Remote"],
      ["Analytics engineer", "Northwind Logistics, Lyon"],
      ["Data platform engineer", "Contoso Rail, Villeurbanne"],
    ].map(
      ([title, where], index) =>
        `<rect x="64" y="${String(190 + index * 150)}" width="900" height="126" rx="12" fill="#ffffff" stroke="#e5e7eb"/>${text(92, 240 + index * 150, 24, title ?? "", 600)}${text(92, 280 + index * 150, 18, where ?? "")}`,
    ),
  ].join(""),
);

/** An application form, as the assistant fills it. */
const FORM = svg(
  [
    text(44, 43, 16, "jobs.example.org/lumen-freight/apply"),
    text(64, 140, 34, "Apply: Data engineer", 600),
    ...[
      ["First name", "Camille"],
      ["Email", "camille@example.org"],
      ["Notice period", "One month"],
      ["Resume", "cv.pdf attached"],
    ].map(
      ([label, value], index) =>
        `${text(64, 210 + index * 140, 18, label ?? "")}<rect x="64" y="${String(226 + index * 140)}" width="760" height="64" rx="10" fill="#ffffff" stroke="#9ca3af"/>${text(88, 268 + index * 140, 22, value ?? "")}`,
    ),
  ].join(""),
);

const PAGES = { results: RESULTS, form: FORM };

class FakeScreen implements Screen {
  viewOnly = true;
  readonly image: HTMLImageElement;
  constructor(image: HTMLImageElement) {
    this.image = image;
  }
  disconnect() {
    this.image.remove();
  }
  focus() {}
  typeKey() {}
  keysymOf(character: string) {
    return character.codePointAt(0) ?? 0;
  }
}

/**
 * Draws an invented page in each live view instead of opening a websocket. `connect`: the view
 * says it connected (else it stays Connecting); `end`: then the lease ends and the view closes;
 * `refuse`: browserd turns the view away (too many open). The pages alternate between search
 * results and a form, in the order the views open. Returns the original, for the story's cleanup.
 */
export function fakeLiveScreens({
  connect = true,
  end = false,
  refuse = false,
  pages = ["results", "form"],
}: {
  connect?: boolean;
  end?: boolean;
  refuse?: boolean;
  pages?: (keyof typeof PAGES)[];
} = {}): () => void {
  const original = screenFactory.open.bind(screenFactory);
  let opened = 0;
  screenFactory.open = (target, _url, events) => {
    const image = document.createElement("img");
    image.alt = "";
    image.src = PAGES[pages[opened % pages.length] ?? "results"];
    image.className = "size-full object-contain";
    opened += 1;
    if (connect && !refuse) target.append(image);
    window.setTimeout(() => {
      if (refuse) {
        events.refused();
        events.disconnect();
        return;
      }
      if (connect) events.connect();
      if (end)
        window.setTimeout(() => {
          events.disconnect();
        }, 400);
    }, 300);
    return Promise.resolve(new FakeScreen(image));
  };
  return () => {
    screenFactory.open = original;
  };
}

/** The live view's routes: a ticket for each view, and who holds the keyboard and the mouse. */
export function liveRoutes({ ticket = true }: { ticket?: boolean } = {}) {
  server.use(
    http.post("*/api/browser/live/ticket", () =>
      ticket
        ? HttpResponse.json(
            {
              ticket: "t".repeat(43),
              url: `/vnc/websockify?ticket=${"t".repeat(43)}`,
              expires_in_s: 30,
            },
            { status: 201 },
          )
        : HttpResponse.json(
            { detail: "You are not using the browser right now." },
            { status: 403 },
          ),
    ),
    http.post("*/api/browser/takeover", () =>
      HttpResponse.json({ input: "you" }),
    ),
    http.post("*/api/browser/giveback", () =>
      HttpResponse.json({ input: "assistant" }),
    ),
  );
}

/** A lease of the person's browser: a sweep's website search unless overridden. */
export function lease(overrides: Partial<Lease> = {}): Lease {
  return {
    id: "5b2d0c7e-8f61-4a3b-9c4d-1e2f3a4b5c6d",
    purpose: "sweep",
    automation: true,
    state: "active",
    wait_reason: null,
    wait_host: null,
    wait_deadline: null,
    started_at: "2026-11-06T09:00:05Z",
    active_s: 42,
    max_s: 86_400,
    application_id: null,
    slot: 0,
    source_id: null,
    ...overrides,
  };
}

/**
 * GET /api/browser: the person's browser holding `held` (none by default), or the leases of its
 * windows when it holds several (two browsers at once), the first one in `lease`.
 */
export function browserHolds(
  held: Lease | Lease[] | null,
  overrides: Partial<Browser> = {},
) {
  const slots = held === null ? [] : Array.isArray(held) ? held : [held];
  server.use(
    http.get("*/api/browser", () =>
      HttpResponse.json({
        available: true,
        profile: { exists: true, size_bytes: 48_000_000, last_used_at: null },
        lease: slots[0] ?? null,
        slots,
        holder: slots.length ? "you" : null,
        queue: [],
        ...overrides,
      } satisfies Browser),
    ),
  );
}
