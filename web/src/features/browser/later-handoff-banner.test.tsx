import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, it, vi } from "vitest";

import { renderWithQuery } from "@/test/render";
import { server } from "@/test/server";

import { LaterHandoffBanner } from "./later-handoff-banner";

vi.mock("./live-browser", () => ({
  LiveBrowser: () => <p>The person's browser</p>,
}));

it("opens only a registered reference and reports the person's done without an address or credential", async () => {
  const bodies: unknown[] = [];
  const lease = {
    id: "fixture-lease",
    purpose: "signin",
    automation: false,
    state: "active",
    wait_reason: null,
    wait_host: null,
    wait_deadline: null,
    wait_minutes_left: null,
    started_at: "2026-10-22T10:00:00Z",
    active_s: 0,
    max_s: 1800,
  };
  server.use(
    http.post("*/api/browser/handoff/later", async ({ request }) => {
      bodies.push(await request.json());
      return HttpResponse.json({ lease, queue: null }, { status: 201 });
    }),
    http.get("*/api/browser", () =>
      HttpResponse.json({
        available: true,
        holder: "you",
        lease,
        queue: [],
        profile: { exists: true, size_bytes: 0, last_used_at: null },
      }),
    ),
    http.post("*/api/browser/handoff/later/done", async ({ request }) => {
      bodies.push(await request.json());
      return new HttpResponse(null, { status: 204 });
    }),
  );
  const refId = "00000000-0000-4000-9000-000000000001";
  renderWithQuery(
    <LaterHandoffBanner
      module="application"
      refId={refId}
      address="https://jobs.example.org/form"
    />,
  );
  const user = userEvent.setup();
  await user.click(
    screen.getByRole("button", { name: "Open the live view to sign in" }),
  );
  await screen.findByText("The person's browser");
  await user.click(screen.getByRole("button", { name: "I'm signed in" }));
  await screen.findByRole("button", { name: "Open the live view to sign in" });
  expect(bodies).toEqual([
    { module: "application", ref: refId },
    { module: "application", ref: refId },
  ]);
});

it("says the browser is busy in the server's own words when other work holds it", async () => {
  const busy =
    "Your browser is busy with something else. Open it again when it is free.";
  server.use(
    http.post("*/api/browser/handoff/later", () =>
      HttpResponse.json({ detail: busy }, { status: 409 }),
    ),
  );
  renderWithQuery(
    <LaterHandoffBanner
      module="application"
      refId="00000000-0000-4000-9000-000000000002"
      address="https://jobs.example.org/form"
    />,
  );
  const user = userEvent.setup();
  await user.click(
    screen.getByRole("button", { name: "Open the live view to sign in" }),
  );
  expect((await screen.findByRole("alert")).textContent).toBe(busy);
  expect(screen.queryByText("The person's browser")).toBeNull();
  expect(
    screen.getByRole("button", { name: "Open the live view to sign in" }),
  ).toBeEnabled();
});
