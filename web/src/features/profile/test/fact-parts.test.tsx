import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { toast } from "sonner";
import { afterEach, describe, expect, it } from "vitest";

import { admin, fakeSession } from "@/test/auth";
import { aboutOf, aboutPartTitles, fakeAbout } from "@/test/about";
import { fakeDocuments } from "@/test/documents";
import { fakeProfile, haraldLike, XSS_BODY } from "@/test/profile";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import type { Facts } from "../api/profile-api";

afterEach(() => {
  toast.dismiss();
});

const RULES = "HARD RULES FOR ANY CV YOU WRITE";
const RULES_BODY =
  "1. Every claim traces to this file.\n2. No invented metric.";
/** Harald's document: his skills, his two fact blocks as parts, a part not saved yet, his answers. */
const TEXT = `## Skills

- agents: agent, langgraph

## ${RULES}

${RULES_BODY}

## POSITIONING

Applied AI for operations.

## Draft

Not saved yet.

## Answers

### Notice period
1 to 3 months.
`;

async function openPage(key: "api_key" | null = "api_key") {
  fakeSession(admin);
  const state = fakeProfile();
  fakeDocuments();
  const about = fakeAbout({ text: TEXT, key });
  const user = userEvent.setup();
  renderApp("/profile/about");
  await screen.findByRole(
    "button",
    { name: "Add a part" },
    { timeout: 10_000 },
  );
  return { user, state, about };
}

const factSaves = (state: ReturnType<typeof fakeProfile>) =>
  state.saves.filter((save) => save.section === "facts");
const titles = (facts: unknown) =>
  (facts as Facts).blocks.map((block) => block.title);
const meter = () => screen.getByRole("meter", { name: "Fact base size" });
const bytes = (value: string) => new TextEncoder().encode(value).length;

describe("the order of the fact parts", () => {
  it("moves a fact part up, saves the order of the fact base and keeps the focus on it", async () => {
    const { user, state, about } = await openPage();
    expect(
      screen.getByRole("button", { name: `Move ${RULES} up` }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Move Draft down" }),
    ).toBeDisabled();
    // Skills and answers do not move: their order means nothing to the profile.
    expect(screen.queryByRole("button", { name: "Move Skills up" })).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Move Answers down" }),
    ).toBeNull();

    await user.click(
      screen.getByRole("button", { name: "Move POSITIONING up" }),
    );
    await waitFor(() => {
      expect(factSaves(state)).toHaveLength(1);
    });
    const saved = factSaves(state)[0]!.body;
    expect(titles(saved.data)).toEqual(["POSITIONING", RULES]);
    expect(saved.data).toEqual({
      blocks: [...haraldLike().facts.blocks].reverse(),
    });
    expect(saved.base_revision).toBe(6);
    // The document follows; the focus stays on the part that moved.
    expect(aboutPartTitles()).toEqual([
      "Skills",
      "POSITIONING",
      RULES,
      "Draft",
      "Answers",
    ]);
    const down = screen.getByRole("button", { name: "Move POSITIONING down" });
    await waitFor(() => {
      expect(down).toHaveFocus();
    });
    expect(screen.getByText("POSITIONING moved up.")).toBeInTheDocument();
    await waitFor(
      () => {
        expect(about.saves.at(-1)?.text.indexOf("## POSITIONING")).toBeLessThan(
          about.saves.at(-1)?.text.indexOf(`## ${RULES}`) ?? 0,
        );
      },
      { timeout: 3000 },
    );
  });

  it("moves by the keyboard, and a part the profile does not hold changes no block", async () => {
    const { user, state } = await openPage();
    screen.getByRole("button", { name: "Move POSITIONING down" }).focus();
    await user.keyboard("{Enter}");
    // Draft holds no block yet: the fact base keeps its order, nothing is saved.
    expect(
      await screen.findByText("POSITIONING moved down."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Move POSITIONING down" }),
    ).toBeDisabled();
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "Move POSITIONING up" }),
      ).toHaveFocus();
    });
    expect(factSaves(state)).toHaveLength(0);
  });

  it("puts the order on the blocks saved elsewhere meanwhile, never their text", async () => {
    const { user, state } = await openPage();
    state.sections.facts = {
      blocks: [
        { title: RULES, body: "Changed in another tab." },
        { title: "POSITIONING", body: XSS_BODY },
        { title: "Elsewhere", body: "Added in another tab." },
      ],
    };
    state.revision = 11;
    await user.click(
      screen.getByRole("button", { name: "Move POSITIONING up" }),
    );
    await waitFor(() => {
      expect(factSaves(state)).toHaveLength(2);
    });
    expect(factSaves(state)[1]!.body).toEqual({
      base_revision: 11,
      data: {
        blocks: [
          { title: "POSITIONING", body: XSS_BODY },
          { title: RULES, body: "Changed in another tab." },
          { title: "Elsewhere", body: "Added in another tab." },
        ],
      },
    });
  });

  it("says so when the order could not be saved", async () => {
    const { user } = await openPage();
    server.use(
      http.put("*/api/profile/facts", () =>
        HttpResponse.json({ detail: "Server error" }, { status: 500 }),
      ),
    );
    await user.click(
      screen.getByRole("button", { name: "Move POSITIONING up" }),
    );
    expect(
      await screen.findByText(/^The order of your fact blocks was not saved\./),
    ).toBeVisible();
  });

  it("does not move a part while one is edited", async () => {
    const { user } = await openPage();
    await user.click(screen.getByRole("button", { name: "Edit Draft" }));
    expect(
      screen.getByRole("button", { name: "Move POSITIONING up" }),
    ).toBeDisabled();
  });

  it("moves the fact parts without a model key too", async () => {
    const { user, state } = await openPage(null);
    await user.click(
      screen.getByRole("button", { name: "Move POSITIONING up" }),
    );
    await waitFor(() => {
      expect(factSaves(state)).toHaveLength(1);
    });
    expect(titles(factSaves(state)[0]!.body.data)).toEqual([
      "POSITIONING",
      RULES,
    ]);
  });
});

describe("the size of the fact base", () => {
  it("measures the fact parts as typed and the blocks no part holds", async () => {
    fakeSession(admin);
    fakeProfile({
      ...haraldLike(),
      facts: {
        blocks: [{ title: "Kept", body: "é" }, ...haraldLike().facts.blocks],
      },
    });
    fakeDocuments();
    fakeAbout();
    server.use(
      http.get("*/api/profile/about", () =>
        HttpResponse.json({
          ...aboutOf(`## Skills\n\n## Notes\n\nabc\n`),
          missing_blocks: ["Kept"],
        }),
      ),
    );
    renderApp("/profile/about");
    await screen.findByRole(
      "meter",
      { name: "Fact base size" },
      {
        timeout: 10_000,
      },
    );
    // "Notes" and "abc" from the document, "Kept" and "é" from the profile.
    const size = bytes("Notes") + bytes("abc") + bytes("Kept") + bytes("é");
    expect(meter()).toHaveAttribute("aria-valuenow", String(size));
    expect(meter()).toHaveAttribute("aria-valuemax", "200000");
    expect(screen.getByText(`${size} bytes of 200 KB`)).toBeVisible();
  });

  it("follows the typing, turns amber near the cap and says what to do over it", async () => {
    const { user } = await openPage();
    await user.click(screen.getByRole("button", { name: "Edit Draft" }));
    const draft = screen.getByRole("region", { name: "Draft" });
    const text = within(draft).getByLabelText("Text");
    const others =
      bytes(RULES) +
      bytes(RULES_BODY) +
      bytes("POSITIONING") +
      bytes("Applied AI for operations.") +
      bytes("Draft");
    // Two bytes a character: just under 90% of the cap, then just under it, then over.
    fireEvent.change(text, { target: { value: "é".repeat(80_000) } });
    expect(meter()).toHaveAttribute("aria-valuenow", String(others + 160_000));
    expect(meter().className).not.toMatch(/bg-warning/);
    fireEvent.change(text, { target: { value: "é".repeat(95_000) } });
    expect(meter().className).toMatch(/bg-warning/);
    expect(screen.queryByText(/^Over the limit/)).toBeNull();
    fireEvent.change(text, { target: { value: "é".repeat(100_000) } });
    expect(meter()).toHaveAttribute("aria-valuenow", "200000");
    expect(meter()).toHaveAttribute(
      "aria-valuetext",
      `${(others + 200_000).toLocaleString("en")} of 200,000 bytes`,
    );
    expect(meter().className).toMatch(/bg-destructive/);
    expect(
      screen.getByText(
        "Over the limit: shorten or remove a fact part before you save it.",
      ),
    ).toBeVisible();
    await user.clear(text);
    expect(screen.queryByText(/^Over the limit/)).toBeNull();
  });
});
