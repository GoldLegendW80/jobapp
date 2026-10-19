import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { toast } from "sonner";
import { afterEach, describe, expect, it } from "vitest";

import { admin, fakeSession } from "@/test/auth";
import { fakeProfile, haraldLike } from "@/test/profile";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import type { DocumentSettings } from "./documents-api";

afterEach(() => {
  toast.dismiss();
});

/** GET and PUT /api/documents/settings, kept in memory; `refuse` answers the next PUT with it,
 * and a PUT waits for `held` while it is set. */
function fakeRules(initial: Partial<DocumentSettings> = {}) {
  const value: DocumentSettings = {
    no_dash_separators: false,
    banned_phrases: [],
    ...initial,
  };
  const state = {
    value,
    puts: [] as DocumentSettings[],
    refuse: undefined as { status: number; detail: string } | undefined,
    held: undefined as Promise<void> | undefined,
  };
  server.use(
    http.get("*/api/documents/settings", () => HttpResponse.json(state.value)),
    http.put("*/api/documents/settings", async ({ request }) => {
      const body = (await request.json()) as DocumentSettings;
      state.puts.push(body);
      if (state.held) await state.held;
      if (state.refuse) {
        const { status, detail } = state.refuse;
        state.refuse = undefined;
        return HttpResponse.json({ detail }, { status });
      }
      state.value = body;
      return HttpResponse.json(state.value);
    }),
  );
  return state;
}

async function openRules(initial: Partial<DocumentSettings> = {}) {
  fakeSession(admin);
  const profile = fakeProfile(haraldLike());
  const rules = fakeRules(initial);
  const user = userEvent.setup();
  renderApp("/profile?tab=search");
  await screen.findByRole("form", { name: "Search" });
  await screen.findByRole("switch", { name: "No dashes as separators" });
  return { user, rules, profile };
}

const card = () =>
  screen.getByRole("heading", { name: "Writing rules" }).closest("section")!;
const dashes = () =>
  screen.getByRole("switch", { name: "No dashes as separators" });
const input = () => screen.getByRole("textbox", { name: "New banned phrase" });
const phrases = () =>
  within(screen.getByRole("list", { name: "Banned phrases" }))
    .getAllByRole("listitem")
    .map((item) => item.textContent);
const DASHES = /[‒–—―−]|\s-\s/u;

describe("writing rules", () => {
  it("starts off for a new user, with no phrase", async () => {
    await openRules();
    expect(dashes()).not.toBeChecked();
    expect(within(card()).getByText("No banned phrase yet.")).toBeVisible();
    expect(within(card()).getByText("0 of 50")).toBeVisible();
    expect(card().textContent).not.toMatch(DASHES);
  });

  it("saves the dash switch at once, apart from the Search section", async () => {
    const { user, rules, profile } = await openRules({
      banned_phrases: ["synergy"],
    });
    await user.click(dashes());
    await screen.findByText("Writing rules saved.");
    expect(rules.puts).toEqual([
      { no_dash_separators: true, banned_phrases: ["synergy"] },
    ]);
    expect(dashes()).toBeChecked();
    expect(profile.saves).toHaveLength(0);
  });

  it("adds a phrase with Enter or Add without saving the Search form, and removes one", async () => {
    const { user, rules, profile } = await openRules({
      banned_phrases: ["synergy"],
    });
    await user.type(input(), "  passionate   about {Enter}");
    await waitFor(() => {
      expect(phrases()).toEqual(["synergy", "passionate about"]);
    });
    expect(input()).toHaveValue("");
    await user.type(input(), "rockstar");
    await user.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => {
      expect(phrases()).toEqual(["synergy", "passionate about", "rockstar"]);
    });
    await user.click(screen.getByRole("button", { name: "Remove synergy" }));
    await waitFor(() => {
      expect(phrases()).toEqual(["passionate about", "rockstar"]);
    });
    expect(rules.puts.map((put) => put.banned_phrases)).toEqual([
      ["synergy", "passionate about"],
      ["synergy", "passionate about", "rockstar"],
      ["passionate about", "rockstar"],
    ]);
    expect(profile.saves).toHaveLength(0);
  });

  it.each(["Synergy", "SYNERGY", " synergy ", "synérgy"])(
    "says %j is already listed and sends nothing",
    async (typed) => {
      const { user, rules } = await openRules({ banned_phrases: ["synergy"] });
      await user.type(input(), `${typed}{Enter}`);
      expect(
        await screen.findByText("This phrase is already in the list."),
      ).toBeVisible();
      expect(input()).toHaveAttribute("aria-invalid", "true");
      expect(rules.puts).toHaveLength(0);
    },
  );

  it("does not add a blank phrase", async () => {
    const { user, rules } = await openRules();
    await user.type(input(), "   {Enter}");
    expect(screen.getByRole("button", { name: "Add" })).toBeDisabled();
    expect(rules.puts).toHaveLength(0);
  });

  it("caps a phrase at 100 characters", async () => {
    await openRules();
    expect(input()).toHaveAttribute("maxLength", "100");
  });

  it("lists 50 phrases, then says why no other can be added", async () => {
    const fifty = Array.from(
      { length: 50 },
      (_, i) => `phrase number ${String(i + 1)}`,
    );
    const { user, rules } = await openRules({ banned_phrases: fifty });
    expect(phrases()).toHaveLength(50);
    expect(within(card()).getByText("50 of 50")).toBeVisible();
    expect(
      within(card()).getByText(
        "You have 50 phrases, the most kept. Remove one to add another.",
      ),
    ).toBeVisible();
    expect(
      screen.queryByRole("textbox", { name: "New banned phrase" }),
    ).toBeNull();
    await user.click(
      screen.getByRole("button", { name: "Remove phrase number 7" }),
    );
    await waitFor(() => {
      expect(phrases()).toHaveLength(49);
    });
    expect(rules.puts[0]!.banned_phrases).toHaveLength(49);
    expect(input()).toBeVisible();
    expect(card().textContent).not.toMatch(DASHES);
  });

  it("shows a change at once, and saves one made during a save after it, in order", async () => {
    const { user, rules } = await openRules({ banned_phrases: ["synergy"] });
    let release = () => {};
    rules.held = new Promise((resolve) => {
      release = resolve;
    });
    await user.click(dashes());
    expect(dashes()).toBeChecked();
    expect(dashes()).toBeEnabled();
    expect(
      screen.getByRole("button", { name: "Remove synergy" }),
    ).toBeEnabled();
    await user.type(input(), "rockstar{Enter}");
    expect(phrases()).toEqual(["synergy", "rockstar"]);
    expect(input()).toHaveValue("");
    expect(dashes()).toBeChecked();
    expect(rules.puts).toHaveLength(1);
    release();
    await waitFor(() => {
      expect(rules.value).toEqual({
        no_dash_separators: true,
        banned_phrases: ["synergy", "rockstar"],
      });
    });
    expect(rules.puts).toEqual([
      { no_dash_separators: true, banned_phrases: ["synergy"] },
      { no_dash_separators: true, banned_phrases: ["synergy", "rockstar"] },
    ]);
    await waitFor(() => {
      expect(card().querySelector('[aria-busy="true"]')).toBeNull();
    });
    expect(dashes()).toBeChecked();
    expect(phrases()).toEqual(["synergy", "rockstar"]);
  });

  it("keeps the phrase being typed when the switch or a Remove is saved", async () => {
    const { user, rules } = await openRules({ banned_phrases: ["synergy"] });
    await user.type(input(), "go get");
    await user.click(dashes());
    await waitFor(() => {
      expect(rules.puts).toHaveLength(1);
    });
    await user.click(screen.getByRole("button", { name: "Remove synergy" }));
    await waitFor(() => {
      expect(rules.value.banned_phrases).toEqual([]);
    });
    await waitFor(() => {
      expect(card().querySelector('[aria-busy="true"]')).toBeNull();
    });
    expect(input()).toHaveValue("go get");
  });

  it("puts the switch back when its save is refused", async () => {
    const { user, rules } = await openRules();
    rules.refuse = { status: 503, detail: "Try again in a moment." };
    await user.click(dashes());
    expect(
      await screen.findByText(
        "The writing rules were not saved. Try again in a moment.",
      ),
    ).toBeVisible();
    expect(dashes()).not.toBeChecked();
    expect(rules.value.no_dash_separators).toBe(false);
  });

  it("shows the api's sentence when a save is refused, and the saved rules", async () => {
    const { user, rules } = await openRules();
    rules.refuse = {
      status: 422,
      detail: "Invalid settings: banned_phrases",
    };
    await user.type(input(), "go getter{Enter}");
    expect(
      await screen.findByText(
        "The writing rules were not saved. Invalid settings: banned_phrases",
      ),
    ).toBeVisible();
    expect(within(card()).getByText("No banned phrase yet.")).toBeVisible();
    expect(input()).toHaveValue("go getter");
    await user.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => {
      expect(phrases()).toEqual(["go getter"]);
    });
  });
});
