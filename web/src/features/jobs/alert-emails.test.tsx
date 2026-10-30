import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { useState } from "react";
import { MemoryRouter } from "react-router";
import { expect, it } from "vitest";

import type { AlertEmails } from "@/features/profile/api";
import { renderWithQuery } from "@/test/render";
import { server } from "@/test/server";

import { AlertEmailsGroup } from "./alert-emails";
import { alertSettings, NO_ALERTS } from "./alert-providers";
import { alertSourceName, sourceLabel, sourceName } from "./labels";

const account = (status: string, canRead: boolean) => ({
  available: true,
  status,
  email: status === "ok" ? "e2e-alerts@example.org" : null,
  can_read: canRead,
  can_send: canRead,
  connected_at: status === "ok" ? "2026-10-01T09:00:00Z" : null,
  last_sync_at: null,
  consent: { granted: false, at: null, version: null, current_version: 2 },
});

function Harness({
  initial,
  seen,
}: {
  initial: AlertEmails;
  seen: AlertEmails[];
}) {
  const [value, setValue] = useState(initial);
  return (
    <AlertEmailsGroup
      id="t"
      value={value}
      onChange={(next) => {
        seen.push(next);
        setValue(next);
      }}
    />
  );
}

function show(initial: AlertEmails = NO_ALERTS) {
  const seen: AlertEmails[] = [];
  renderWithQuery(
    <MemoryRouter>
      <Harness initial={initial} seen={seen} />
    </MemoryRouter>,
  );
  return seen;
}

it("lists one box per provider with its how-to, the saved value ticked", async () => {
  show({ indeed: true, linkedin: false });
  const group = screen.getByRole("region", { name: "Alert e-mails" });
  const indeed = within(group).getByRole("checkbox", {
    name: "Indeed alert e-mails",
  });
  expect(indeed).toBeChecked();
  expect(indeed).toHaveAccessibleDescription(
    "Create a daily job alert on fr.indeed.com for each title; the app reads only these alert e-mails.",
  );
  expect(
    within(group).getByRole("checkbox", { name: "LinkedIn alert e-mails" }),
  ).not.toBeChecked();
  expect(within(group).getByText(/never opens their links/)).toBeVisible();
  // The default fake account: Gmail not connected, with the way to connect it.
  expect(
    await within(group).findByText(/Gmail is not connected/),
  ).toBeVisible();
  expect(
    within(group).getByText(/on the Account page \(Gmail card\)/),
  ).toBeVisible();
  // No link: leaving the dialog would lose the draft.
  expect(within(group).queryByRole("link")).toBeNull();
});

it("ticking and unticking a box changes that provider only", async () => {
  const seen = show();
  const user = userEvent.setup();
  await user.click(
    screen.getByRole("checkbox", { name: "LinkedIn alert e-mails" }),
  );
  await user.click(
    screen.getByRole("checkbox", { name: "Indeed alert e-mails" }),
  );
  await user.click(
    screen.getByRole("checkbox", { name: "LinkedIn alert e-mails" }),
  );
  expect(seen).toEqual([
    { indeed: false, linkedin: true },
    { indeed: true, linkedin: true },
    { indeed: true, linkedin: false },
  ]);
});

it("says when Gmail is connected", async () => {
  server.use(
    http.get("*/api/mail/account", () =>
      HttpResponse.json(account("ok", true)),
    ),
  );
  show();
  expect(
    await screen.findByText(
      "Gmail is connected: ticked alert e-mails are read at each search.",
    ),
  ).toBeVisible();
});

it("asks to connect again when Gmail needs it, or when reading is not allowed", async () => {
  server.use(
    http.get("*/api/mail/account", () =>
      HttpResponse.json(account("needs_reconnect", true)),
    ),
  );
  show();
  expect(
    await screen.findByText(/Gmail needs to be connected again/),
  ).toBeVisible();
});

it("a connection without the read permission is not connected for alerts", async () => {
  server.use(
    http.get("*/api/mail/account", () =>
      HttpResponse.json(account("ok", false)),
    ),
  );
  show();
  expect(await screen.findByText(/Gmail is not connected/)).toBeVisible();
});

it("a failed check says so and never claims a connection", async () => {
  server.use(
    http.get("*/api/mail/account", () =>
      HttpResponse.json({ detail: "boom" }, { status: 500 }),
    ),
  );
  show();
  expect(
    await screen.findByText("Whether Gmail is connected could not be checked."),
  ).toBeVisible();
  expect(screen.queryByText(/^Gmail is connected/)).toBeNull();
});

it("a search saved before the setting reads every provider off", () => {
  expect(alertSettings(undefined)).toEqual(NO_ALERTS);
  expect(alertSettings({ indeed: true, linkedin: false })).toEqual({
    indeed: true,
    linkedin: false,
  });
});

it("names alert e-mail sources in words", () => {
  expect(alertSourceName("mail:indeed")).toBe("Indeed alert e-mails");
  expect(alertSourceName("mail:other")).toBeNull();
  expect(alertSourceName("toString")).toBeNull();
  expect(sourceName({ kind: "alerts", query: "", host: "mail:linkedin" })).toBe(
    "LinkedIn alert e-mails",
  );
  expect(sourceLabel("jobs.example.org, mail:indeed")).toBe(
    "jobs.example.org; Indeed alert e-mails",
  );
});
