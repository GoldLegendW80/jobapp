import { screen, waitFor } from "@testing-library/react";
import type { QueryClient } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import { browserQueryKey, type Lease } from "@/features/browser/browser-api";
import { FREE, connected, fakeScreens } from "@/test/browser";
import { renderWithQuery } from "@/test/render";
import { server } from "@/test/server";

import { LiveViewSlot, type LeaseOf } from "./live-view-slot";

afterEach(() => {
  vi.restoreAllMocks();
});

const STARTED = "2026-10-24T09:00:00Z";
const SUBMITS = "The assistant fills the form again, then submits it.";
/** Two applications of the person, both approved: a submit lease names the one it sends. */
const MINE = "00000000-0000-4000-9000-0000000000a1";
const OTHER = "00000000-0000-4000-9000-0000000000a2";
const OF_MINE: LeaseOf = { application: MINE };

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

function slot(leaseOf: LeaseOf, submit = true) {
  return renderWithQuery(
    <LiveViewSlot
      idle="Waiting for the browser."
      leaseOf={leaseOf}
      status={SUBMITS}
      submit={submit}
    />,
  );
}

/** The slot has read the browser once, so what it shows is its answer to that read. */
async function read(client: QueryClient) {
  await waitFor(() => {
    expect(client.getQueryData(browserQueryKey)).toBeDefined();
  });
}

describe("the submit lease", () => {
  it.each([
    ["working", "active", null],
    ["paused on a captcha", "waiting_person", "captcha"],
    ["paused on a sign in", "waiting_person", "signin"],
  ] as const)(
    "is shown watch only while %s: no Take over, no keyboard",
    async (_, state, reason) => {
      const opened = fakeScreens();
      browser(lease({ state, wait_reason: reason }));
      slot(OF_MINE);
      const view = await connected(opened);
      expect(view.screen.viewOnly).toBe(true);
      expect(await screen.findByRole("status")).toHaveTextContent(SUBMITS);
      for (const name of ["Take over", "Give back", "Keyboard"]) {
        expect(screen.queryByRole("button", { name })).toBeNull();
      }
      expect(screen.queryByLabelText("Type into your browser")).toBeNull();
      expect(
        screen.queryByRole("button", { name: "I'm signed in" }),
      ).toBeNull();
      expect(
        screen.getByRole("button", { name: "Full screen" }),
      ).toBeInTheDocument();
    },
  );

  it.each([
    ["another application's", lease({ application_id: OTHER })],
    ["one that names no application", lease({ application_id: null })],
    ["one without the field", lease({ application_id: undefined })],
    ["a lease still starting", lease({ state: "starting" })],
    ["a fill lease", lease({ purpose: "apply_fill" })],
    ["a Full auto fill lease", lease({ purpose: "full_auto" })],
    ["a sweep", lease({ purpose: "sweep" })],
    ["a profile edit", lease({ purpose: "profile_edit" })],
    ["the person's own session", lease({ purpose: "look", automation: false })],
  ])("is not %s", async (_, found) => {
    const opened = fakeScreens();
    browser(found);
    const { client } = slot(OF_MINE);
    await read(client);
    expect(screen.getByTestId("live-view-slot")).toHaveTextContent(
      "Waiting for the browser.",
    );
    expect(screen.queryByTestId("live-screen")).toBeNull();
    expect(opened).toHaveLength(0);
  });

  it("never shows another person's lease", async () => {
    const opened = fakeScreens();
    browser(lease(), "other");
    const { client } = slot(OF_MINE);
    await read(client);
    expect(screen.getByTestId("live-view-slot")).toHaveTextContent(
      "Waiting for the browser.",
    );
    expect(opened).toHaveLength(0);
  });

  it.each([
    ["does not read", "none", "idle"],
    ["reads", "any", "fetching"],
  ] as const)(
    "%s the browser while leaseOf is %s",
    (_, leaseOf, fetchStatus) => {
      browser(lease());
      const { client } = slot(leaseOf);
      expect(client.getQueryState(browserQueryKey)?.fetchStatus).toBe(
        fetchStatus,
      );
    },
  );
});

describe("the fill slot", () => {
  it("does not show the submit lease", async () => {
    const opened = fakeScreens();
    browser(lease());
    const { client } = slot("any", false);
    await read(client);
    expect(screen.queryByTestId("live-screen")).toBeNull();
    expect(opened).toHaveLength(0);
  });

  it("still offers Take over on its own fill lease", async () => {
    const opened = fakeScreens();
    browser(lease({ purpose: "apply_fill" }));
    slot("any", false);
    await connected(opened);
    expect(
      await screen.findByRole("button", { name: "Take over" }),
    ).toBeEnabled();
    expect(screen.getByLabelText("Type into your browser")).toBeInTheDocument();
  });
});
