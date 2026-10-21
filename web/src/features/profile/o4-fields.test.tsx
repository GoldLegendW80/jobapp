/**
 * Opportunities O4 in the Search tab: the seniority, the pay and, under Advanced, the industries
 * and the company types, saved with the search section; the Preferences card saving the language
 * and the tone as its own section, sending the onboarding dates back as loaded.
 */
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";
import { afterEach, describe, expect, it } from "vitest";

import { admin, fakeSession } from "@/test/auth";
import { fakeProfile, haraldLike } from "@/test/profile";
import { renderApp } from "@/test/render";

import type { Preferences, Search } from "./api";

afterEach(() => {
  toast.dismiss();
});

async function open(initial = haraldLike()) {
  fakeSession(admin);
  const state = fakeProfile(initial);
  const user = userEvent.setup();
  renderApp("/profile?tab=search");
  await screen.findByRole("form", { name: "Search" });
  return { user, state };
}

const searchForm = () => screen.getByRole("form", { name: "Search" });
// The first Advanced of the tab is this one; the documents card has its own below.
const advanced = () =>
  within(searchForm()).getAllByRole("button", { name: "Advanced" })[0]!;
const lastSave = (state: ReturnType<typeof fakeProfile>, section: string) =>
  state.saves.filter((s) => s.section === section).at(-1)?.body.data;

describe("what you aim for", () => {
  it("shows the stored values and saves seniority, pay, industries and types", async () => {
    const { user, state } = await open();
    const form = within(searchForm());
    expect(form.getByRole("button", { name: "Senior" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(form.getByLabelText("From Yearly salary")).toHaveValue("55000");
    await user.click(form.getByRole("button", { name: "Lead" }));
    await user.type(form.getByLabelText("From Daily rate (freelance)"), "500");
    await user.type(form.getByLabelText("To Daily rate (freelance)"), "650");
    await user.click(advanced());
    await user.type(
      form.getByLabelText("New term for Industries"),
      "fintech{enter}",
    );
    await user.click(form.getByRole("button", { name: "Startup" }));
    await user.click(form.getByRole("button", { name: "Save search" }));
    await screen.findByText("Search saved.");
    const data = lastSave(state, "search") as Search;
    expect(data.seniority).toEqual(["senior", "lead"]);
    expect(data.industries.terms).toEqual(["gaming", "fintech"]);
    expect(data.company_types).toEqual(["startup", "scaleup"]);
    expect(data.compensation).toEqual({
      salary: { min: 55000, max: 70000, currency: "EUR" },
      daily_rate: { min: 500, max: 650, currency: "EUR" },
    });
  });

  it.each([
    ["700", "650", "The maximum is below the minimum."],
    ["-5", "650", "Enter a whole number from 0 to 10,000,000."],
    ["5.5", "650", "Enter a whole number from 0 to 10,000,000."],
    ["500", "", "Enter a whole number from 0 to 10,000,000."],
    ["500", "10000001", "Enter a whole number from 0 to 10,000,000."],
  ])("refuses a daily rate from %s to %s", async (min, max, message) => {
    const { user, state } = await open();
    const form = within(searchForm());
    await user.type(form.getByLabelText("From Daily rate (freelance)"), min);
    if (max)
      await user.type(form.getByLabelText("To Daily rate (freelance)"), max);
    await user.click(form.getByRole("button", { name: "Save search" }));
    expect(await form.findByText(message)).toBeVisible();
    expect(lastSave(state, "search")).toBeUndefined();
  });

  it("clears a pay when both figures are emptied", async () => {
    const { user, state } = await open();
    const form = within(searchForm());
    await user.clear(form.getByLabelText("From Yearly salary"));
    await user.clear(form.getByLabelText("To Yearly salary"));
    await user.click(form.getByRole("button", { name: "Save search" }));
    await screen.findByText("Search saved.");
    expect(
      (lastSave(state, "search") as Search).compensation.salary,
    ).toBeNull();
  });

  it("opens Advanced on an industries error", async () => {
    const sections = haraldLike();
    const terms = Array.from({ length: 30 }, (_, i) => `sector${String(i)}`);
    const { user } = await open({
      ...sections,
      search: {
        ...sections.search,
        industries: { advanced_regex: false, terms },
      },
    });
    const form = within(searchForm());
    await user.click(advanced());
    await user.type(
      form.getByLabelText("New term for Industries"),
      "one more{enter}",
    );
    await user.click(advanced());
    expect(advanced()).toHaveAttribute("aria-expanded", "false");
    await user.click(form.getByRole("button", { name: "Save search" }));
    expect(await form.findByText("Keep at most 30 industries.")).toBeVisible();
    expect(advanced()).toHaveAttribute("aria-expanded", "true");
  });
});

describe("preferences card", () => {
  it("saves the language and the tone as its own section", async () => {
    const { user, state } = await open();
    const card = within(screen.getByRole("form", { name: "Preferences" }));
    await user.selectOptions(
      card.getByLabelText("Language of documents"),
      "fr",
    );
    await user.selectOptions(card.getByLabelText("Tone of documents"), "warm");
    await user.click(card.getByRole("button", { name: "Save preferences" }));
    await screen.findByText("Preferences saved.");
    expect(lastSave(state, "preferences")).toEqual({
      onboarding: { completed_at: null, dismissed_at: null },
      documents_language: "fr",
      documents_tone: "warm",
    } satisfies Preferences);
    expect(lastSave(state, "search")).toBeUndefined();
  });

  it("sends the wizard's dates back unchanged", async () => {
    const sections = haraldLike();
    const dates = {
      completed_at: "2026-10-20T08:00:00+00:00",
      dismissed_at: null,
    };
    const { user, state } = await open({
      ...sections,
      preferences: { ...sections.preferences, onboarding: dates },
    });
    const card = within(screen.getByRole("form", { name: "Preferences" }));
    await user.selectOptions(
      card.getByLabelText("Tone of documents"),
      "formal",
    );
    await user.click(card.getByRole("button", { name: "Save preferences" }));
    await screen.findByText("Preferences saved.");
    expect((lastSave(state, "preferences") as Preferences).onboarding).toEqual(
      dates,
    );
  });
});
