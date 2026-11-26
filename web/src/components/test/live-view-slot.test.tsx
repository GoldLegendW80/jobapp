import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { QueryClient } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  browserQueryKey,
  type Lease,
} from "@/features/browser/api/browser-api";
import {
  FREE,
  connected,
  fakeScreens,
  fakeWindows,
  windowIn,
} from "@/test/browser";
import { fakeApplication } from "@/test/apply";
import { renderWithQuery } from "@/test/render";
import { server } from "@/test/server";

import {
  LiveViewSlot,
  type LeaseOf,
} from "@/components/shared/apply/live-view-slot";
import { liveStatus, liveWatchOnly } from "@/features/apply/helpers/live-view";

afterEach(() => {
  vi.restoreAllMocks();
});

const STARTED = "2026-10-24T09:00:00Z";
/** Two applications of the person: a lease names the one it fills or sends. */
const MINE = "00000000-0000-4000-9000-0000000000a1";
const OTHER = "00000000-0000-4000-9000-0000000000a2";
const OF_MINE: LeaseOf = { application: MINE };
const IDLE = "Waiting for the browser.";
const FILL_PURPOSES = [
  "apply_fill",
  "full_auto",
  "apply_agent",
  "apply_review",
];
const STATES = ["filling", "approved", "submitting"];
const TAKE_OVER = [
  "Use the browser myself",
  "Let the assistant continue",
  "Keyboard",
];

function lease(overrides: Partial<Lease> = {}): Lease {
  return {
    id: "3c5d7e9f-1a2b-4c3d-8e4f-5a6b7c8d9e0f",
    purpose: "apply_submit",
    automation: true,
    state: "active",
    wait_reason: null,
    wait_host: null,
    wait_deadline: null,
    started_at: STARTED,
    active_s: 12,
    max_s: 900,
    application_id: MINE,
    ...overrides,
  };
}

/** The person's browser as GET /api/browser answers it. */
function browser(found: Lease | null, holder: "you" | "other" | null = "you") {
  server.use(
    http.get("*/api/browser", () =>
      HttpResponse.json({ ...FREE, holder, lease: found }),
    ),
  );
}

/** The slot of an application in `state`, as the job page and the Applications page mount it. */
function slot(
  leaseOf: LeaseOf,
  {
    state = "filling",
    mode = "direct",
  }: { state?: string; mode?: string } = {},
) {
  const run = fakeApplication(state, { id: MINE, mode });
  return renderWithQuery(
    <LiveViewSlot
      idle={IDLE}
      leaseOf={leaseOf}
      status={(purpose) => liveStatus(purpose, run)}
      watchOnly={(purpose) => liveWatchOnly(purpose, run)}
      namedOnly={state !== "filling"}
    />,
  );
}

/** The live view shows the window watch only: no take over, no keyboard. */
function watchedOnly() {
  for (const name of TAKE_OVER) {
    expect(screen.queryByRole("button", { name })).toBeNull();
  }
  expect(screen.queryByLabelText("Type into your browser")).toBeNull();
}

/** The slot has read the browser once, so what it shows is its answer to that read. */
async function read(client: QueryClient) {
  await waitFor(() => {
    expect(client.getQueryData(browserQueryKey)).toBeDefined();
  });
}

describe("the lease the api names for the application, whatever its state", () => {
  // Review first's agent after the Confirm is watch only (below).
  const cases = STATES.flatMap((state) =>
    FILL_PURPOSES.map((purpose) => [purpose, state] as const),
  ).filter(
    ([purpose, state]) => purpose !== "apply_review" || state === "filling",
  );

  it.each(cases)(
    "shows a fill of purpose %s while %s, with Use the browser myself",
    async (purpose, state) => {
      const opened = fakeScreens();
      browser(lease({ purpose }));
      slot(OF_MINE, { state });
      const view = await connected(opened);
      expect(await screen.findByRole("status")).toHaveTextContent(
        liveStatus(purpose, fakeApplication(state, { mode: "direct" })),
      );
      expect(
        screen.getByRole("button", { name: "Use the browser myself" }),
      ).toBeEnabled();
      expect(
        screen.getByLabelText("Type into your browser"),
      ).toBeInTheDocument();
      expect(view.screen.viewOnly).toBe(true);
      expect(screen.queryByText(IDLE)).toBeNull();
    },
  );

  it.each(STATES)(
    "shows the submit lease watch only while %s",
    async (state) => {
      const opened = fakeScreens();
      browser(lease());
      slot(OF_MINE, { state, mode: "review" });
      const view = await connected(opened);
      expect(view.screen.viewOnly).toBe(true);
      expect(await screen.findByRole("status")).toHaveTextContent(
        "The assistant fills the form again with your approved answers, then submits it.",
      );
      watchedOnly();
      expect(
        screen.getByRole("button", { name: "Full screen" }),
      ).toBeInTheDocument();
    },
  );

  it.each([
    ["working", "active", null],
    ["paused on a captcha", "waiting_person", "captcha"],
    ["paused on a sign in", "waiting_person", "signin"],
  ] as const)(
    "keeps the submit lease watch only while %s",
    async (_, state, reason) => {
      const opened = fakeScreens();
      browser(lease({ state, wait_reason: reason }));
      slot(OF_MINE, { state: "submitting", mode: "review" });
      const view = await connected(opened);
      expect(view.screen.viewOnly).toBe(true);
      for (const name of TAKE_OVER) {
        expect(screen.queryByRole("button", { name })).toBeNull();
      }
      expect(
        screen.queryByRole("button", { name: "I'm signed in" }),
      ).toBeNull();
    },
  );

  it("says what the agent does now: filling the form, then sending it", async () => {
    const opened = fakeScreens();
    browser(lease({ purpose: "apply_agent" }));
    const { client, rerender } = slot(OF_MINE);
    await connected(opened);
    expect(await screen.findByRole("status")).toHaveTextContent(
      "The assistant is filling the form. It sends it by itself once it is filled.",
    );
    const sending = fakeApplication("submitting", { id: MINE, mode: "direct" });
    rerender(
      <LiveViewSlot
        idle={IDLE}
        leaseOf={OF_MINE}
        status={(purpose) => liveStatus(purpose, sending)}
        namedOnly
      />,
    );
    await read(client);
    expect(screen.getByRole("status")).toHaveTextContent(
      "The assistant is sending the form. It may still fill a step or two first.",
    );
    expect(
      screen.getByRole("button", { name: "Use the browser myself" }),
    ).toBeEnabled();
    expect(opened).toHaveLength(1);
  });
});

describe("Review first's agent once the person confirmed", () => {
  const SENDS =
    "The app sends the form you confirmed. The assistant then reads the site's answer.";

  it.each(["approved", "submitting"])(
    "shows its lease watch only while %s",
    async (state) => {
      const opened = fakeScreens();
      browser(lease({ purpose: "apply_review" }));
      slot(OF_MINE, { state, mode: "review" });
      const view = await connected(opened);
      expect(await screen.findByRole("status")).toHaveTextContent(SENDS);
      expect(view.screen.viewOnly).toBe(true);
      watchedOnly();
      expect(
        screen.getByRole("button", { name: "Full screen" }),
      ).toBeInTheDocument();
    },
  );

  it("keeps its lease watch only while it waits on the page after the Confirm", async () => {
    const opened = fakeScreens();
    browser(
      lease({
        purpose: "apply_review",
        state: "waiting_person",
        wait_reason: "captcha",
        wait_host: "jobs.example.org",
      }),
    );
    slot(OF_MINE, { state: "submitting", mode: "review" });
    const view = await connected(opened);
    expect(await screen.findByRole("status")).toHaveTextContent(SENDS);
    expect(view.screen.viewOnly).toBe(true);
    watchedOnly();
  });

  it.each([
    [
      "apply_review",
      "filling",
      "review",
      "The assistant is filling the form. Then you check what it filled and confirm.",
    ],
    [
      "apply_agent",
      "submitting",
      "direct",
      "The assistant is sending the form. It may still fill a step or two first.",
    ],
  ])(
    "keeps Use the browser myself for a lease of %s while %s",
    async (purpose, state, mode, sentence) => {
      const opened = fakeScreens();
      browser(lease({ purpose }));
      slot(OF_MINE, { state, mode });
      await connected(opened);
      expect(await screen.findByRole("status")).toHaveTextContent(sentence);
      expect(
        screen.getByRole("button", { name: "Use the browser myself" }),
      ).toBeEnabled();
      expect(
        screen.getByLabelText("Type into your browser"),
      ).toBeInTheDocument();
    },
  );

  it("keeps the submit lease watch only whatever the caller's rule says", async () => {
    const opened = fakeScreens();
    browser(lease());
    renderWithQuery(
      <LiveViewSlot idle={IDLE} leaseOf={OF_MINE} watchOnly={() => false} />,
    );
    const view = await connected(opened);
    expect(view.screen.viewOnly).toBe(true);
    watchedOnly();
  });
});

describe("no lease of the application's", () => {
  it.each(STATES)("says what it waits for while %s", async (state) => {
    const opened = fakeScreens();
    browser(null, null);
    const { client } = slot(OF_MINE, { state });
    await read(client);
    expect(screen.getByTestId("live-view-slot")).toHaveTextContent(IDLE);
    expect(opened).toHaveLength(0);
  });

  it.each(
    STATES.flatMap((state) =>
      [...FILL_PURPOSES, "apply_submit"].map((purpose) => [purpose, state]),
    ),
  )(
    "never shows another application's lease of purpose %s while %s",
    async (purpose, state) => {
      const opened = fakeScreens();
      browser(lease({ purpose, application_id: OTHER }));
      const { client } = slot(OF_MINE, { state });
      await read(client);
      expect(screen.getByTestId("live-view-slot")).toHaveTextContent(IDLE);
      expect(screen.queryByTestId("live-screen")).toBeNull();
      expect(opened).toHaveLength(0);
    },
  );

  it.each([
    [
      "a submit lease that names no application",
      lease({ application_id: null }),
    ],
    ["one without the field", lease({ application_id: undefined })],
    ["a lease still starting", lease({ state: "starting" })],
    ["a sweep", lease({ purpose: "sweep" })],
    ["a profile edit", lease({ purpose: "profile_edit" })],
    [
      "Apply myself's session",
      lease({ purpose: "apply_manual", automation: false }),
    ],
    ["the person's own session", lease({ purpose: "look", automation: false })],
  ])("is not %s", async (_, found) => {
    const opened = fakeScreens();
    browser(found);
    const { client } = slot(OF_MINE, { state: "submitting" });
    await read(client);
    expect(screen.getByTestId("live-view-slot")).toHaveTextContent(IDLE);
    expect(opened).toHaveLength(0);
  });

  it.each(["approved", "submitting"])(
    "takes no fill that names no application for its own while %s",
    async (state) => {
      const opened = fakeScreens();
      browser(lease({ purpose: "apply_agent", application_id: null }));
      const { client } = slot(OF_MINE, { state });
      await read(client);
      expect(screen.getByTestId("live-view-slot")).toHaveTextContent(IDLE);
      expect(opened).toHaveLength(0);
    },
  );

  it("never shows another person's lease", async () => {
    const opened = fakeScreens();
    browser(lease(), "other");
    const { client } = slot(OF_MINE);
    await read(client);
    expect(screen.getByTestId("live-view-slot")).toHaveTextContent(IDLE);
    expect(opened).toHaveLength(0);
  });

  it.each([
    ["does not read", "none", "idle"],
    ["reads", OF_MINE, "fetching"],
  ] as const)(
    "%s the browser while leaseOf is %o",
    (_, leaseOf, fetchStatus) => {
      browser(lease());
      const { client } = slot(leaseOf);
      expect(client.getQueryState(browserQueryKey)?.fetchStatus).toBe(
        fetchStatus,
      );
    },
  );
});

describe("the fill of the apply agent", () => {
  it("says what the agent asks the person on the page", async () => {
    const opened = fakeScreens();
    browser(
      lease({
        purpose: "apply_agent",
        state: "waiting_person",
        wait_reason: "ask_person",
        wait_host: "jobs.example.org",
      }),
    );
    slot(OF_MINE, { state: "submitting" });
    await connected(opened);
    expect(await screen.findByRole("status")).toHaveTextContent(
      "The assistant needs your help on jobs.example.org.",
    );
  });

  it("gives the keyboard and the mouse for a captcha of a fill, whatever its state", async () => {
    const opened = fakeScreens();
    browser(
      lease({
        purpose: "apply_fill",
        state: "waiting_person",
        wait_reason: "captcha",
        wait_host: "jobs.example.org",
      }),
    );
    slot(OF_MINE, { state: "approved", mode: "review" });
    await connected(opened);
    expect(await screen.findByRole("status")).toHaveTextContent(
      "You have the keyboard and the mouse. Complete this step in the live view.",
    );
  });

  it("keeps a fixed sentence when the caller gives one", async () => {
    const opened = fakeScreens();
    browser(lease({ purpose: "apply_fill" }));
    renderWithQuery(
      <LiveViewSlot idle={IDLE} leaseOf={OF_MINE} status="Fixed sentence." />,
    );
    await connected(opened);
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Fixed sentence.",
    );
  });
});

describe("a slot in a grid of views", () => {
  function labelled(leaseOf: LeaseOf) {
    return renderWithQuery(
      <LiveViewSlot
        label="Robotics Engineer"
        detail="Example Robotics"
        idle="Waiting for your browser."
        leaseOf={leaseOf}
      />,
    );
  }
  const VIEW = "Live view of your browser: Robotics Engineer";

  it("names its application and says it is live", async () => {
    const opened = fakeScreens();
    browser(lease({ purpose: "apply_fill" }));
    labelled(OF_MINE);
    await connected(opened);
    const view = screen.getByRole("region", { name: VIEW });
    expect(view).toHaveTextContent("Example Robotics");
    expect(await within(view).findByText("Live")).toBeInTheDocument();
  });

  it("says why its screen is not shown yet, under the same name", async () => {
    const opened = fakeScreens();
    browser(null, null);
    const { client } = labelled(OF_MINE);
    await read(client);
    const view = screen.getByRole("region", { name: VIEW });
    expect(view).toHaveTextContent("Waiting for your browser.");
    expect(within(view).getByText("Waiting")).toBeInTheDocument();
    expect(opened).toHaveLength(0);
  });

  it("sends a sign in to the application's card instead of a second view", async () => {
    const opened = fakeScreens();
    browser(
      lease({
        purpose: "apply_fill",
        state: "waiting_person",
        wait_reason: "signin",
        wait_host: "jobs.example.org",
      }),
    );
    labelled(OF_MINE);
    await waitFor(() => {
      expect(screen.getByRole("region", { name: VIEW })).toHaveTextContent(
        "The assistant waits for you to sign in to jobs.example.org. Sign in from this application's card.",
      );
    });
    expect(
      within(screen.getByRole("region", { name: VIEW })).getByText("Needs you"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "I'm signed in" })).toBeNull();
    expect(opened).toHaveLength(0);
  });
});

describe("two browsers at once", () => {
  it("shows this application's lease among the person's windows, and asks the ticket for its slot", async () => {
    const opened = fakeScreens();
    const asked: unknown[] = [];
    server.use(
      http.post("*/api/browser/live/ticket", async ({ request }) => {
        asked.push(await request.json());
        return HttpResponse.json(
          {
            ticket: "t".repeat(43),
            url: `/vnc/websockify?ticket=${"t".repeat(43)}`,
            expires_in_s: 30,
          },
          { status: 201 },
        );
      }),
    );
    const search = lease({
      id: "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d",
      purpose: "sweep",
      application_id: null,
      slot: 0,
    });
    const mine = lease({ slot: 1 });
    server.use(
      http.get("*/api/browser", () =>
        HttpResponse.json({
          ...FREE,
          holder: "you",
          lease: search,
          slots: [search, mine],
        }),
      ),
    );
    slot(OF_MINE);
    await connected(opened);
    expect(asked).toHaveLength(1);
    expect(asked[0]).toMatchObject({ slot: 1 });
  });
});

describe("two fills of the person at once", () => {
  /** The fill of the other application in window 0, and of `mine` in window 1. */
  function twoFills(mine: Partial<Lease> = {}) {
    const other = lease({
      purpose: "apply_fill",
      application_id: OTHER,
      slot: 0,
    });
    const own = lease({
      id: "4d6e8f0a-2b3c-4d5e-9f6a-7b8c9d0e1f2a",
      purpose: "apply_agent",
      slot: 1,
      ...mine,
    });
    server.use(
      http.get("*/api/browser", () =>
        HttpResponse.json({
          ...FREE,
          holder: "you",
          lease: other,
          slots: [other, own],
        }),
      ),
    );
  }

  it("shows this application's window, and takes over that window", async () => {
    const { opened, takeovers } = fakeWindows();
    twoFills();
    const user = userEvent.setup();
    slot(OF_MINE);
    await waitFor(() => {
      expect(opened).toHaveLength(1);
    });
    expect(windowIn(opened, screen.getByTestId("live-view-slot"))).toBe(1);
    act(() => {
      opened[0]?.events.connect();
    });
    await user.click(
      await screen.findByRole("button", { name: "Use the browser myself" }),
    );
    await waitFor(() => {
      expect(takeovers).toEqual([1]);
    });
  });

  it("never shows the other application's fill, even when it started first", async () => {
    const opened = fakeScreens();
    twoFills({ application_id: OTHER });
    const { client } = slot(OF_MINE);
    await read(client);
    expect(screen.getByTestId("live-view-slot")).toHaveTextContent(IDLE);
    expect(opened).toHaveLength(0);
  });

  it.each([
    ["is the person's one fill", [null], true],
    ["is beside another fill", [null, OTHER], false],
    ["is one of two", [null, null], false],
  ])(
    "shows a fill lease that names no application only if it %s",
    async (_, named, shown) => {
      const opened = fakeScreens();
      const leases = named.map((application_id, at) =>
        lease({
          id: `4d6e8f0a-2b3c-4d5e-9f6a-7b8c9d0e1f2${String(at)}`,
          purpose: "apply_fill",
          application_id,
          slot: at,
        }),
      );
      server.use(
        http.get("*/api/browser", () =>
          HttpResponse.json({
            ...FREE,
            holder: "you",
            lease: leases[0],
            slots: leases,
          }),
        ),
      );
      const { client } = slot(OF_MINE);
      await read(client);
      await waitFor(() => {
        expect(opened).toHaveLength(shown ? 1 : 0);
      });
      expect(screen.queryByText(IDLE) === null).toBe(shown);
    },
  );
});
