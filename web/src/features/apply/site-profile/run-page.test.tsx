import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it, vi } from "vitest";
import * as browserApi from "@/features/browser/browser-api";
import {
  browserQueryKey,
  type Browser,
  type Lease,
} from "@/features/browser/browser-api";
import { fakeScreens, FREE, ownLease } from "@/test/browser";
import { server } from "@/test/server";
import { runKey, type ProfileRun } from "./api";
import { SiteProfileRunPage } from "./run-page";

function fixture(): ProfileRun {
  return {
    id: "run-a",
    url: "https://portal.example/profile",
    host: "portal.example",
    site_kind: "generic",
    state: "filling",
    autosave: false,
    autosave_confirmed: false,
    needs: null,
    error_code: null,
    created_at: "2026-10-22T00:00:00Z",
    updated_at: "2026-10-22T00:00:00Z",
    cost: {},
    sections: [],
  };
}

function held(changes: Partial<Lease> = {}): Browser {
  return {
    ...FREE,
    holder: "you",
    lease: {
      ...ownLease("look"),
      purpose: "profile_edit",
      automation: true,
      ...changes,
    },
  };
}

function show(
  initial = held(),
  refuse = false,
  browserResponse?: Promise<void>,
  cached?: Browser,
) {
  const state = { browser: initial, run: fixture(), browserResponse, refuse };
  const opened = fakeScreens();
  const tickets: unknown[] = [];
  server.use(
    http.get("*/api/site-profiles/:id", ({ params }) =>
      HttpResponse.json({ ...state.run, id: params.id }),
    ),
    http.get("*/api/browser", async () => {
      await state.browserResponse;
      return state.refuse
        ? HttpResponse.json({ detail: "Browser unavailable." }, { status: 403 })
        : HttpResponse.json(state.browser);
    }),
    http.post("*/api/browser/live/ticket", async ({ request }) => {
      tickets.push(await request.json());
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
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  if (cached) client.setQueryData(browserQueryKey, cached);
  const router = createMemoryRouter(
    [{ path: "/site-profiles/:id", element: <SiteProfileRunPage /> }],
    { initialEntries: ["/site-profiles/run-a"] },
  );
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  const refresh = async () => {
    await act(async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: browserQueryKey }),
        client.invalidateQueries({ queryKey: runKey(state.run.id) }),
      ]);
    });
  };
  return { state, opened, tickets, client, router, refresh };
}

describe("site profile run live view readiness", () => {
  it.each([
    [
      "active snapshot before starting",
      held({ id: "old-lease" }),
      held({ id: "starting:turn-a", state: "starting" }),
    ],
    ["active snapshot before no lease", held({ id: "old-lease" }), FREE],
    [
      "sign-in snapshot before no lease",
      held({
        id: "old-lease",
        state: "waiting_person",
        wait_reason: "signin",
        wait_host: "portal.example",
      }),
      FREE,
    ],
  ] as const)(
    "waits for a fresh browser response with cached %s",
    async (_name, cached, fresh) => {
      let finish = () => {};
      const pending = new Promise<void>((resolve) => {
        finish = resolve;
      });
      const { state, opened, tickets, refresh } = show(
        fresh,
        false,
        pending,
        cached,
      );
      try {
        await screen.findByRole("heading", {
          name: "Complete your profile on portal.example",
        });
        expect(
          screen.queryByRole("region", { name: "Live view of your browser" }),
        ).not.toBeInTheDocument();
        expect(tickets).toEqual([]);
        finish();
        await refresh();
        expect(
          screen.queryByRole("region", { name: "Live view of your browser" }),
        ).not.toBeInTheDocument();
        expect(tickets).toEqual([]);
        state.browser = held({ id: "current-lease" });
        await refresh();
        await waitFor(() => {
          expect(opened).toHaveLength(1);
        });
        expect(tickets).toHaveLength(1);
      } finally {
        finish();
      }
    },
  );

  it("requires a fresh browser observation after the route changes run id", async () => {
    const { state, opened, tickets, router, refresh } = show(
      held({ id: "lease-a" }),
    );
    await waitFor(() => {
      expect(opened).toHaveLength(1);
    });
    let finish = () => {};
    state.browserResponse = new Promise<void>((resolve) => {
      finish = resolve;
    });
    state.browser = held({ id: "starting:turn-b", state: "starting" });
    state.run = { ...state.run, id: "run-b" };
    try {
      await act(async () => {
        await router.navigate("/site-profiles/run-b");
      });
      await screen.findByRole("heading", {
        name: "Complete your profile on portal.example",
      });
      expect(opened[0]!.screen.disconnects).toBe(1);
      expect(
        screen.queryByRole("region", { name: "Live view of your browser" }),
      ).not.toBeInTheDocument();
      expect(tickets).toHaveLength(1);
      finish();
      await refresh();
      expect(tickets).toHaveLength(1);
      state.browser = held({ id: "lease-b" });
      await refresh();
      await waitFor(() => {
        expect(opened).toHaveLength(2);
      });
      expect(tickets).toHaveLength(2);
    } finally {
      finish();
    }
  });

  it("retires a cached active view when fresh browser readiness is refused", async () => {
    const { state, opened, tickets, refresh } = show();
    await waitFor(() => {
      expect(opened).toHaveLength(1);
    });
    state.refuse = true;
    await refresh();
    await waitFor(() => {
      expect(
        screen.queryByRole("region", { name: "Live view of your browser" }),
      ).not.toBeInTheDocument();
    });
    expect(opened[0]!.screen.disconnects).toBe(1);
    expect(tickets).toHaveLength(1);
  });

  it("does not request a ticket before browser data arrives", async () => {
    let finish = () => {};
    const pending = new Promise<void>((resolve) => {
      finish = resolve;
    });
    const { opened, tickets, refresh } = show(FREE, false, pending);
    try {
      await screen.findByRole("heading", {
        name: "Complete your profile on portal.example",
      });
      expect(
        screen.queryByRole("region", { name: "Live view of your browser" }),
      ).not.toBeInTheDocument();
      expect(tickets).toEqual([]);
      finish();
      await refresh();
      expect(tickets).toEqual([]);
      expect(opened).toEqual([]);
    } finally {
      finish();
    }
  });

  it("waits for its starting lease, opens once when ready, and follows lease replacement", async () => {
    const { state, opened, tickets, refresh } = show(
      held({ id: "starting:turn-a", state: "starting" }),
    );
    await screen.findByRole("heading", {
      name: "Complete your profile on portal.example",
    });
    await refresh();
    expect(
      screen.queryByRole("region", { name: "Live view of your browser" }),
    ).not.toBeInTheDocument();
    expect(tickets).toEqual([]);
    expect(opened).toEqual([]);
    await refresh();
    expect(tickets).toEqual([]);
    state.browser = held({ id: "lease-a" });
    await refresh();
    await waitFor(() => {
      expect(opened).toHaveLength(1);
    });
    expect(tickets).toHaveLength(1);
    state.run.state = "saving";
    await refresh();
    await refresh();
    expect(tickets).toHaveLength(1);
    expect(opened).toHaveLength(1);
    state.browser = held({ id: "lease-b" });
    await refresh();
    await waitFor(() => {
      expect(opened).toHaveLength(2);
    });
    expect(tickets).toHaveLength(2);
    expect(tickets[1]).not.toEqual(tickets[0]);
    expect(opened[0]!.screen.disconnects).toBe(1);
    state.browser = FREE;
    await refresh();
    await waitFor(() => {
      expect(
        screen.queryByRole("region", { name: "Live view of your browser" }),
      ).not.toBeInTheDocument();
    });
    expect(opened[1]!.screen.disconnects).toBe(1);
    expect(tickets).toHaveLength(2);
  });

  it.each([
    ["missing", FREE, false],
    ["foreign", { ...held(), holder: "other" as const }, false],
    ["starting", held({ state: "starting" }), false],
    ["another purpose", held({ purpose: "apply_fill" }), false],
    ["a person session", held({ automation: false }), false],
    ["refused", FREE, true],
  ] as const)(
    "does not request a ticket for %s browser state",
    async (_name, browser, refuse) => {
      const { tickets, opened, refresh } = show(browser, refuse);
      await screen.findByRole("heading", {
        name: "Complete your profile on portal.example",
      });
      await refresh();
      expect(
        screen.queryByRole("region", { name: "Live view of your browser" }),
      ).not.toBeInTheDocument();
      expect(tickets).toEqual([]);
      expect(opened).toEqual([]);
    },
  );

  it.each(["active", "waiting_person"] as const)(
    "allows its own %s profile-edit lease",
    async (state) => {
      const { opened, tickets, refresh } = show(held({ state }));
      await waitFor(() => {
        expect(opened).toHaveLength(1);
      });
      await refresh();
      expect(tickets).toHaveLength(1);
      expect(
        screen.getAllByRole("region", { name: "Live view of your browser" }),
      ).toHaveLength(1);
    },
  );

  it("preserves the sign-in handoff with exactly one live-view producer", async () => {
    const { opened, tickets, refresh } = show(
      held({
        state: "waiting_person",
        wait_reason: "signin",
        wait_host: "portal.example",
      }),
    );
    await screen.findByRole("heading", { name: "Sign in to portal.example" });
    await waitFor(() => {
      expect(opened).toHaveLength(1);
    });
    await refresh();
    expect(tickets).toHaveLength(1);
    expect(
      screen.getAllByRole("region", { name: "Live view of your browser" }),
    ).toHaveLength(1);
    act(() => {
      opened[0]!.events.connect();
    });
    expect(
      screen.getByText(/You have the keyboard and the mouse: sign in/),
    ).toBeVisible();
  });

  it.each(["queued", "ready", "needs_person", "done", "failed", "cancelled"])(
    "unmounts the filling view when the run becomes %s",
    async (next) => {
      const { state, opened, tickets, refresh } = show();
      await waitFor(() => {
        expect(opened).toHaveLength(1);
      });
      state.run = {
        ...state.run,
        state: next,
        needs: next === "needs_person" ? "autosave_confirmation" : null,
      };
      await refresh();
      await waitFor(() => {
        expect(
          screen.queryByRole("region", { name: "Live view of your browser" }),
        ).not.toBeInTheDocument();
      });
      expect(opened[0]!.screen.disconnects).toBe(1);
      expect(tickets).toHaveLength(1);
    },
  );

  it("does not open a screen for a ticket resolving after the lease disappears", async () => {
    const ticketCall = vi.spyOn(browserApi, "liveTicket");
    let finish = () => {};
    const pending = new Promise<void>((resolve) => {
      finish = resolve;
    });
    const { state, opened, tickets, refresh } = show();
    server.use(
      http.post("*/api/browser/live/ticket", async ({ request }) => {
        tickets.push(await request.json());
        await pending;
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
    try {
      await waitFor(() => {
        expect(tickets).toHaveLength(1);
      });
      state.browser = FREE;
      await refresh();
      await waitFor(() => {
        expect(
          screen.queryByRole("region", { name: "Live view of your browser" }),
        ).not.toBeInTheDocument();
      });
      await act(async () => {
        finish();
        await ticketCall.mock.results[0]!.value;
      });
      expect(opened).toEqual([]);
      expect(tickets).toHaveLength(1);
    } finally {
      finish();
    }
  });
});
