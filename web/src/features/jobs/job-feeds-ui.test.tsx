import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";

import { renderWithQuery } from "@/test/render";
import { server } from "@/test/server";

import { AdzunaKeyCard, SweepJobFeeds } from "./job-feeds-ui";
import type { SweepSource } from "./sweeps-api";

function entry(kind: string, extra: Partial<SweepSource> = {}): SweepSource {
  return {
    kind,
    mode: "api",
    query: "",
    location: null,
    result_pages: 1,
    found: 0,
    new: 0,
    skipped_by_rule: 0,
    items_skipped: 0,
    error: null,
    ...extra,
  };
}

it("lists each job feed of a sweep with its counts and why it stopped", () => {
  renderWithQuery(
    <SweepJobFeeds
      sources={[
        entry("custom", { mode: "llm_browser" }),
        entry("arbeitnow", { found: 3, new: 2 }),
        entry("himalayas", {
          found: 1,
          new: 1,
          searches: 2,
          error: "rate_limited",
        }),
        entry("adzuna", { error: "no_key", reason: "no_key" }),
      ]}
    />,
  );
  const list = screen.getByRole("region", { name: "Job feeds" });
  const items = within(list).getAllByRole("listitem");
  expect(items.map((item) => item.textContent)).toEqual([
    "Arbeitnow: 3 postings kept, 2 new.",
    "Himalayas: 2 searches. 1 posting kept, 1 new. The site asked the app to slow down, so it is read again at the next search. What was read is kept.",
    "Adzuna: Skipped: no Adzuna key is stored. Add your free key in Account to search Adzuna.",
  ]);
});

it("shows nothing without a job feed", () => {
  renderWithQuery(
    <SweepJobFeeds sources={[entry("custom", { mode: "llm_browser" })]} />,
  );
  expect(screen.queryByRole("region", { name: "Job feeds" })).toBeNull();
});

it("stores the Adzuna key, never shows it back, and removes it", async () => {
  let stored = false;
  const puts: unknown[] = [];
  let deletes = 0;
  const state = () => ({
    feeds: [
      { name: "arbeitnow", needs_key: false },
      { name: "himalayas", needs_key: false },
      {
        name: "adzuna",
        needs_key: true,
        key_stored: stored,
        key_last4: stored ? "k042" : null,
        key_saved_at: stored ? "2026-10-30T10:00:00Z" : null,
      },
    ],
    keys_available: true,
  });
  server.use(
    http.get("*/api/jobs/feeds", () => HttpResponse.json(state())),
    http.put("*/api/jobs/feeds/adzuna/key", async ({ request }) => {
      puts.push(await request.json());
      stored = true;
      return HttpResponse.json(state().feeds[2]);
    }),
    http.delete("*/api/jobs/feeds/adzuna/key", () => {
      deletes += 1;
      stored = false;
      return new HttpResponse(null, { status: 204 });
    }),
  );
  renderWithQuery(<AdzunaKeyCard />);
  const user = userEvent.setup();
  expect(
    await screen.findByText("No key is stored: Adzuna is skipped."),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("link", { name: "developer.adzuna.com" }),
  ).toHaveAttribute("href", "https://developer.adzuna.com/signup");
  await user.click(screen.getByRole("button", { name: "Save key" }));
  expect(screen.getByRole("alert")).toHaveTextContent(
    "Paste both the application ID and the application key.",
  );
  expect(puts).toEqual([]);
  const secret = "q".repeat(28) + "k042";
  await user.type(screen.getByLabelText("Application ID"), " abcd1234 ");
  await user.type(screen.getByLabelText("Application key"), secret);
  expect(screen.getByLabelText("Application key")).toHaveAttribute(
    "type",
    "password",
  );
  await user.click(screen.getByRole("button", { name: "Save key" }));
  await waitFor(() => {
    expect(puts).toEqual([{ app_id: "abcd1234", app_key: secret }]);
  });
  expect(
    await screen.findByText(/A key ending in k042 is stored/),
  ).toBeInTheDocument();
  expect(screen.getByLabelText("Application key")).toHaveValue("");
  expect(document.body.textContent).not.toContain(secret);
  await user.click(screen.getByRole("button", { name: "Remove key" }));
  expect(
    await screen.findByText("No key is stored: Adzuna is skipped."),
  ).toBeInTheDocument();
  expect(deletes).toBe(1);
});

it("words a refused key without echoing it", async () => {
  server.use(
    http.put("*/api/jobs/feeds/adzuna/key", () =>
      HttpResponse.json(
        {
          detail:
            "The application key holds only letters and digits, at least 16, as Adzuna shows it.",
        },
        { status: 422 },
      ),
    ),
  );
  renderWithQuery(<AdzunaKeyCard />);
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Application ID"), "abcd1234");
  await user.type(screen.getByLabelText("Application key"), "short key");
  await user.click(screen.getByRole("button", { name: "Save key" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "The application key holds only letters and digits, at least 16, as Adzuna shows it.",
  );
  expect(screen.getByRole("alert")).not.toHaveTextContent("short key");
});
