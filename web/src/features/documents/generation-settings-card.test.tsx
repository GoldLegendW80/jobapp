import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { toast } from "sonner";
import { afterEach, describe, expect, it } from "vitest";

import type { Search } from "@/features/profile/api";
import { admin, fakeSession } from "@/test/auth";
import { fakeProfile, haraldLike, type FakeProfile } from "@/test/profile";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

afterEach(() => {
  toast.dismiss();
});

const QUOTA =
  "On a Claude subscription, tokens count against your quota, not as money.";

interface Spent {
  input: number;
  output: number;
  cost_usd: string;
}
const NONE: Spent = { input: 0, output: 0, cost_usd: "0" };

/** GET /api/documents/spend as the api answers it: the counts at the threshold asked (the saved
 * one when none is), from the fits of the person's postings of the last 30 days, none written. */
function fakeSpend(
  state: FakeProfile,
  {
    fits = [] as number[],
    written = 0,
    waiting = 0,
    priced = false,
    today = NONE,
    month = NONE,
  } = {},
) {
  const asked: (number | null)[] = [];
  server.use(
    http.get("*/api/documents/spend", ({ request }) => {
      const param = new URL(request.url).searchParams.get("min_fit");
      asked.push(param === null ? null : Number(param));
      const stored = (state.sections.search as Search).documents;
      const minFit = param === null ? stored.min_fit : Number(param);
      const at = fits.filter((fit) => fit >= minFit);
      const newly = at.filter((fit) => fit < stored.min_fit).length;
      const estimate = (count: number) => ({
        tokens: { low: count * 1000, high: count * 2000 },
        cost: priced
          ? {
              currency: "USD",
              low: (count * 0.1).toFixed(2),
              high: (count * 0.2).toFixed(2),
            }
          : null,
      });
      return HttpResponse.json({
        day: "2026-10-15",
        auto: stored.auto,
        min_fit: minFit,
        daily_ceiling: stored.daily_ceiling,
        written_today: written,
        waiting,
        postings_30_days: at.length,
        to_write: at.length,
        to_write_estimate: estimate(at.length),
        newly,
        newly_estimate: estimate(newly),
        priced,
        cost_note: priced ? "Estimates from list prices." : QUOTA,
        today,
        last_30_days: month,
      });
    }),
  );
  return asked;
}

async function openSearch(spend: Parameters<typeof fakeSpend>[1] = {}) {
  fakeSession(admin);
  const state = fakeProfile(haraldLike());
  const asked = fakeSpend(state, spend);
  const user = userEvent.setup();
  renderApp("/profile?tab=search");
  await screen.findByRole("form", { name: "Search" });
  return { user, state, asked };
}

const card = () =>
  screen
    .getByRole("heading", { name: "Documents for new postings" })
    .closest("section")!;
const save = () => screen.getByRole("button", { name: "Save search" });
const saved = (state: FakeProfile, index = 0) =>
  (state.saves[index]!.body.data as Search).documents;
const slider = () =>
  screen.getByRole("slider", { name: "Fit score threshold" });
const ceiling = () => screen.getByLabelText("Safety ceiling per day");
const openAdvanced = async (user: ReturnType<typeof userEvent.setup>) => {
  await user.click(within(card()).getByRole("button", { name: "Advanced" }));
};
const DASHES = /[‒–—―−]|\s-\s/u;

describe("documents for new postings: the card", () => {
  it("shows the switch, the sentence, today against the ceiling, the waiting and the spend", async () => {
    await openSearch({
      written: 3,
      waiting: 2,
      today: { input: 1000, output: 200, cost_usd: "0.12" },
      month: { input: 40000, output: 5000, cost_usd: "1.5" },
    });
    expect(
      screen.getByRole("switch", { name: "Write documents automatically" }),
    ).toBeChecked();
    expect(
      within(card()).getByText("For postings with a fit of 70 or more."),
    ).toBeVisible();
    const today = await screen.findByTestId("documents-today");
    expect(today).toHaveTextContent(
      "Today: 3 postings written, the safety ceiling is 100.",
    );
    expect(today).toHaveTextContent("2 postings waiting.");
    const spend = await screen.findByTestId("documents-spend");
    expect(spend).toHaveTextContent("Tokens today1,200");
    expect(spend).toHaveTextContent("Tokens over 30 days45,000");
    expect(spend).toHaveTextContent(
      "Estimate at API pricesUSD 0.12 today, USD 1.50 over 30 days",
    );
    expect(spend).toHaveTextContent(QUOTA);
    expect(card().textContent).not.toMatch(DASHES);
  });

  it("calls the amount a cost on an API key", async () => {
    await openSearch({
      priced: true,
      today: { input: 10, output: 5, cost_usd: "0.01" },
      month: { input: 10, output: 5, cost_usd: "0.01" },
    });
    const spend = await screen.findByTestId("documents-spend");
    expect(spend).toHaveTextContent(
      "CostUSD 0.01 today, USD 0.01 over 30 days",
    );
    expect(spend).not.toHaveTextContent("Estimate at API prices");
  });

  it("says nothing was spent when no run exists, and that nothing waits", async () => {
    await openSearch();
    expect(await screen.findByTestId("documents-spend")).toHaveTextContent(
      "Nothing spent on documents yet.",
    );
    const today = screen.getByTestId("documents-today");
    expect(today).toHaveTextContent(
      "Today: 0 postings written, the safety ceiling is 100.",
    );
    expect(today).toHaveTextContent("No posting is waiting.");
    expect(today).not.toHaveTextContent("ceiling is reached");
  });

  it("says when today's safety ceiling is reached", async () => {
    await openSearch({ written: 100, waiting: 1 });
    const today = await screen.findByTestId("documents-today");
    expect(today).toHaveTextContent(
      "Today's safety ceiling is reached: new postings wait until tomorrow.",
    );
    expect(today).toHaveTextContent("1 posting waiting.");
  });

  it("switched off, says nothing is written automatically and saves it with the section", async () => {
    const { user, state } = await openSearch();
    await user.click(
      screen.getByRole("switch", { name: "Write documents automatically" }),
    );
    expect(
      within(card()).getByText(
        'No documents are written automatically. "Write now" on a posting still writes its CV and letter.',
      ),
    ).toBeVisible();
    expect(card().textContent).not.toMatch(DASHES);
    await user.click(save());
    await screen.findByText("Search saved.");
    expect(saved(state)).toEqual({
      auto: false,
      min_fit: 70,
      daily_ceiling: 100,
    });
  });
});

describe("documents for new postings: Advanced", () => {
  it("keeps the threshold and the ceiling folded until Advanced is opened", async () => {
    const { user } = await openSearch();
    const button = within(card()).getByRole("button", { name: "Advanced" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(
      screen.getByRole("slider", { name: "Fit score threshold", hidden: true }),
    ).not.toBeVisible();
    await openAdvanced(user);
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(slider()).toBeVisible();
    expect(ceiling()).toHaveValue("100");
    expect(
      screen.getByText(
        "Protects you from a broken sweep. The threshold is what limits spend.",
      ),
    ).toBeVisible();
  });

  it("shows what a lower threshold adds before it is saved, then saves it", async () => {
    const { user, state, asked } = await openSearch({
      fits: [95, 80, 70, 69, 60, 59, 10],
    });
    await openAdvanced(user);
    const figures = screen.getByTestId("documents-threshold");
    expect(
      await within(figures).findByText(
        "3 postings of the last 30 days have a fit of 70 or more.",
      ),
    ).toBeVisible();
    fireEvent.change(slider(), { target: { value: "60" } });
    expect(
      within(card()).getByText("For postings with a fit of 60 or more."),
    ).toBeVisible();
    expect(
      await within(figures).findByText(
        "5 postings of the last 30 days have a fit of 60 or more.",
      ),
    ).toBeVisible();
    expect(screen.getByTestId("documents-newly")).toHaveTextContent(
      "Lowering it to 60 adds 2 postings to write, about 2,000 to 4,000 tokens.",
    );
    expect(asked).toContain(60);
    expect(state.saves).toHaveLength(0);
    expect(card().textContent).not.toMatch(DASHES);

    await user.click(save());
    await screen.findByText("Search saved.");
    expect(saved(state)).toEqual({
      auto: true,
      min_fit: 60,
      daily_ceiling: 100,
    });
    await waitFor(() => {
      expect(screen.queryByTestId("documents-newly")).toBeNull();
    });
    expect(
      await within(figures).findByText(
        "5 postings of the last 30 days have a fit of 60 or more.",
      ),
    ).toBeVisible();
  });

  it("gives the cost of what a lower threshold adds on an API key, and says when it adds none", async () => {
    const { user } = await openSearch({ fits: [90, 65], priced: true });
    await openAdvanced(user);
    fireEvent.change(slider(), { target: { value: "65" } });
    expect(await screen.findByTestId("documents-newly")).toHaveTextContent(
      "Lowering it to 65 adds 1 posting to write, about 1,000 to 2,000 tokens, about USD 0.10 to USD 0.20.",
    );
    fireEvent.change(slider(), { target: { value: "66" } });
    await waitFor(() => {
      expect(screen.getByTestId("documents-newly")).toHaveTextContent(
        "Lowering it to 66 adds no posting to write.",
      );
    });
  });

  it("warns with the count at 0, and at 100 says only a perfect fit qualifies", async () => {
    const { user } = await openSearch({ fits: [100, 80, 5, 0] });
    await openAdvanced(user);
    const figures = screen.getByTestId("documents-threshold");
    fireEvent.change(slider(), { target: { value: "0" } });
    expect(
      await within(figures).findByText(
        "At 0 the threshold holds nothing back: 4 postings of the last 30 days have a fit score.",
      ),
    ).toBeVisible();
    expect(screen.getByTestId("documents-newly")).toHaveTextContent(
      "Lowering it to 0 adds 2 postings to write",
    );
    fireEvent.change(slider(), { target: { value: "100" } });
    expect(
      await within(figures).findByText(
        "1 posting of the last 30 days has a fit of 100 or more.",
      ),
    ).toBeVisible();
    expect(
      within(figures).getByText("At 100 only a perfect fit qualifies."),
    ).toBeVisible();
    expect(
      within(figures).getByText("Raising it removes nothing already written."),
    ).toBeVisible();
    expect(screen.queryByTestId("documents-newly")).toBeNull();
    expect(card().textContent).not.toMatch(DASHES);
  });

  it("at 0 with a single scored posting, says it has a fit score", async () => {
    const { user } = await openSearch({ fits: [40] });
    await openAdvanced(user);
    fireEvent.change(slider(), { target: { value: "0" } });
    expect(
      await within(screen.getByTestId("documents-threshold")).findByText(
        "At 0 the threshold holds nothing back: 1 posting of the last 30 days has a fit score.",
      ),
    ).toBeVisible();
  });

  it.each(["501", "600", "0", "", "  ", "12.5", "1e2", "-3", "abc", "1000"])(
    "refuses a safety ceiling of %j with the sentence and saves nothing",
    async (value) => {
      const { user, state } = await openSearch();
      await openAdvanced(user);
      await user.clear(ceiling());
      if (value !== "") await user.type(ceiling(), value);
      await user.click(save());
      expect(
        await screen.findByText(
          "The safety ceiling is a whole number of postings a day, from 1 to 500.",
        ),
      ).toBeVisible();
      expect(state.saves).toHaveLength(0);
    },
  );

  it.each([
    ["1", 1],
    ["500", 500],
    [" 250 ", 250],
  ])("saves a safety ceiling of %j", async (value, expected) => {
    const { user, state } = await openSearch();
    await openAdvanced(user);
    await user.clear(ceiling());
    await user.type(ceiling(), value);
    await user.click(save());
    await screen.findByText("Search saved.");
    expect(saved(state).daily_ceiling).toBe(expected);
  });

  it("opens Advanced again to show a refused ceiling after it was folded", async () => {
    const { user, state } = await openSearch();
    await openAdvanced(user);
    await user.clear(ceiling());
    await user.type(ceiling(), "501");
    await openAdvanced(user);
    expect(ceiling()).not.toBeVisible();
    await user.click(save());
    expect(
      await screen.findByText(
        "The safety ceiling is a whole number of postings a day, from 1 to 500.",
      ),
    ).toBeVisible();
    expect(ceiling()).toBeVisible();
    expect(state.saves).toHaveLength(0);
  });

  it("shows the profile's conflict dialog on a stale revision, and keeps the threshold", async () => {
    const { user, state } = await openSearch({ fits: [80, 60] });
    await openAdvanced(user);
    fireEvent.change(slider(), { target: { value: "55" } });
    state.revision = 12;
    await user.click(save());
    const dialog = await screen.findByRole("alertdialog", {
      name: "This section changed elsewhere",
    });
    await user.click(
      within(dialog).getByRole("button", { name: "Keep editing" }),
    );
    expect(slider()).toHaveValue("55");
    await user.click(save());
    await screen.findByText("Search saved.");
    expect(state.saves.map((s) => s.body.base_revision)).toEqual([6, 12]);
    expect(saved(state, 1).min_fit).toBe(55);
  });
});
