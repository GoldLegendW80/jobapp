import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { toast } from "sonner";
import { afterEach, describe, expect, it } from "vitest";

import { admin, fakeSession } from "@/test/auth";
import { fakeProfile, haraldLike, XSS_BODY } from "@/test/profile";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import type { Facts } from "./api";

afterEach(() => {
  toast.dismiss();
});

async function openFacts(
  initial = haraldLike() as Parameters<typeof fakeProfile>[0],
) {
  fakeSession(admin);
  const state = fakeProfile(initial);
  const user = userEvent.setup();
  const app = renderApp("/profile?tab=facts");
  await screen.findByRole("heading", { level: 1, name: "Profile" });
  await screen.findByRole("form", { name: "Facts" });
  return { user, state, ...app };
}

const blockButton = (name: RegExp) => screen.getByRole("button", { name });
const save = () => screen.getByRole("button", { name: "Save facts" });
const meter = () => screen.getByRole("meter", { name: "Fact base size" });
const savedFacts = (state: ReturnType<typeof fakeProfile>, index = 0) =>
  state.saves[index]!.body.data as Facts;

describe("facts tab", () => {
  it("lists the blocks collapsed and measures the fact base", async () => {
    await openFacts();
    const hard = blockButton(/^HARD RULES FOR ANY CV YOU WRITE, block 1$/);
    expect(hard).toHaveAttribute("aria-expanded", "false");
    expect(blockButton(/^POSITIONING, block 2$/)).toBeVisible();
    expect(screen.queryByLabelText("Title of block 1")).toBeNull();
    const bytes = haraldLike().facts.blocks.reduce(
      (sum, b) => sum + new TextEncoder().encode(b.title + b.body).length,
      0,
    );
    expect(meter()).toHaveAttribute("aria-valuenow", String(bytes));
    expect(meter()).toHaveAttribute("aria-valuemax", "200000");
    expect(screen.getByText(`${bytes} bytes of 200 KB`)).toBeVisible();
  });

  it("opens a block with its editor and a safe Markdown preview", async () => {
    const { user } = await openFacts();
    await user.click(blockButton(/POSITIONING, block 2/));
    expect(blockButton(/POSITIONING, block 2/)).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(screen.getByLabelText("Title of block 2")).toHaveValue(
      "POSITIONING",
    );
    expect(screen.getByLabelText("Text of block 2")).toHaveValue(XSS_BODY);
    const preview = screen.getByRole("region", { name: "Preview of block 2" });
    expect(preview.querySelector("script")).toBeNull();
    expect(preview.querySelector("img")).toBeNull();
    expect(preview.textContent).toContain("<script>");
    expect(within(preview).getByText("bold").tagName).toBe("STRONG");
    expect(
      within(preview).getByRole("link", { name: "safe link" }),
    ).toHaveAttribute("href", "https://example.org");
    expect(
      within(preview).queryByRole("link", { name: "bad link" }),
    ).toBeNull();
    expect((window as { __xss?: string }).__xss).toBeUndefined();

    await user.click(blockButton(/POSITIONING, block 2/));
    expect(screen.queryByLabelText("Text of block 2")).toBeNull();
  });

  it("updates the preview live and switches Write and Preview on small screens", async () => {
    const { user } = await openFacts();
    await user.click(blockButton(/, block 1$/));
    const text = screen.getByLabelText("Text of block 1");
    await user.clear(text);
    await user.type(text, "## Freight{enter}{enter}- **Talvo** first");
    const preview = screen.getByRole("region", { name: "Preview of block 1" });
    expect(
      within(preview).getByRole("heading", { name: "Freight" }),
    ).toBeVisible();
    expect(within(preview).getByText("Talvo").tagName).toBe("STRONG");
    expect(within(preview).getByRole("listitem")).toHaveTextContent(
      "Talvo first",
    );

    const views = screen.getByRole("group", { name: "Editor view of block 1" });
    const write = within(views).getByRole("button", { name: "Write" });
    const show = within(views).getByRole("button", { name: "Preview" });
    expect(write).toHaveAttribute("aria-pressed", "true");
    // Below md only one pane shows: the preview is hidden while writing.
    expect(preview).toHaveClass("max-md:hidden");
    await user.click(show);
    expect(show).toHaveAttribute("aria-pressed", "true");
    expect(preview).not.toHaveClass("max-md:hidden");
    expect(text.closest('[data-slot="form-item"]')).toHaveClass(
      "max-md:hidden",
    );
  });

  it("adds a block, opens it, focuses its title and saves it trimmed", async () => {
    const { user, state } = await openFacts();
    await user.click(screen.getByRole("button", { name: "Add block" }));
    const title = await screen.findByLabelText("Title of block 3");
    await waitFor(() => {
      expect(title).toHaveFocus();
    });
    await user.type(title, "  WHAT WAS BUILT ");
    await user.type(
      screen.getByLabelText("Text of block 3"),
      "An *agent-driven* back-office.{enter}{enter}",
    );
    expect(blockButton(/^WHAT WAS BUILT, block 3$/)).toBeVisible();
    expect(screen.getByText("Unsaved changes")).toBeVisible();
    await user.click(save());
    expect(await screen.findByText("Facts saved.")).toBeVisible();
    const blocks = savedFacts(state).blocks;
    expect(blocks).toHaveLength(3);
    expect(blocks[2]).toEqual({
      title: "WHAT WAS BUILT",
      body: "An *agent-driven* back-office.",
    });
    expect(state.saves[0]!.body.base_revision).toBe(6);
  });

  it("reorders and removes blocks", async () => {
    const { user, state } = await openFacts();
    await user.click(screen.getByRole("button", { name: "Move block 1 down" }));
    expect(blockButton(/^POSITIONING, block 1$/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Remove block 1" }));
    expect(screen.queryByText("POSITIONING")).toBeNull();
    await user.click(save());
    await screen.findByText("Facts saved.");
    expect(savedFacts(state).blocks.map((b) => b.title)).toEqual([
      "HARD RULES FOR ANY CV YOU WRITE",
    ]);
  });

  it("removes every block and saves an empty fact base", async () => {
    const { user, state } = await openFacts();
    await user.click(screen.getByRole("button", { name: "Remove block 2" }));
    await user.click(screen.getByRole("button", { name: "Remove block 1" }));
    expect(screen.getByText("No fact block yet.")).toBeVisible();
    expect(meter()).toHaveAttribute("aria-valuenow", "0");
    await user.click(save());
    await screen.findByText("Facts saved.");
    expect(savedFacts(state)).toEqual({ blocks: [] });
  });

  it("refuses empty fields and duplicate titles before sending, opening the block", async () => {
    const { user, state } = await openFacts();
    await user.click(screen.getByRole("button", { name: "Add block" }));
    await user.click(save());
    expect(await screen.findByText("Enter a title.")).toBeVisible();
    expect(screen.getByText("Enter the text of this block.")).toBeVisible();
    await user.type(screen.getByLabelText("Title of block 3"), "positioning");
    await user.type(screen.getByLabelText("Text of block 3"), "x");
    await user.click(save());
    // Block 2 was collapsed: it opens to show nothing wrong, block 3 carries the error.
    expect(
      await screen.findByText("Another block already has this title."),
    ).toBeVisible();
    expect(screen.getByLabelText("Title of block 3")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    expect(state.saves).toHaveLength(0);
  });

  it("measures UTF-8 bytes and refuses a fact base over 200 KB", async () => {
    const { user, state } = await openFacts({
      ...haraldLike(),
      facts: { blocks: [{ title: "Big", body: "x" }] },
    });
    await user.click(blockButton(/Big, block 1/));
    // 99,999 two-byte characters: 199,998 bytes plus the title, under the cap.
    fireEvent.change(screen.getByLabelText("Text of block 1"), {
      target: { value: "é".repeat(99_998) },
    });
    expect(meter()).toHaveAttribute("aria-valuenow", String(3 + 199_996));
    expect(screen.getByText("199,999 of 200,000 bytes")).toBeVisible();
    fireEvent.change(screen.getByLabelText("Text of block 1"), {
      target: { value: "é".repeat(100_000) },
    });
    // Over the cap: the meter stays full and the count turns red.
    expect(meter()).toHaveAttribute("aria-valuenow", "200000");
    expect(meter()).toHaveAttribute(
      "aria-valuetext",
      "200,003 of 200,000 bytes",
    );
    await user.click(save());
    expect(
      await screen.findByText(
        /^The fact base is 200,003 bytes, at most 200,000 allowed\./,
      ),
    ).toBeVisible();
    expect(state.saves).toHaveLength(0);
  });

  it("shows the server's size refusal above the blocks, not on a title", async () => {
    const { user } = await openFacts();
    server.use(
      http.put("*/api/profile/facts", () =>
        HttpResponse.json(
          {
            detail:
              "data.blocks: the fact base is 200123 bytes, at most 200000 allowed",
          },
          { status: 422 },
        ),
      ),
    );
    await user.click(screen.getByRole("button", { name: "Move block 1 down" }));
    await user.click(save());
    expect(
      await screen.findByText(
        "The server refused this section: data.blocks: the fact base is 200123 bytes, at most 200000 allowed",
      ),
    ).toBeVisible();
    expect(screen.queryByLabelText("Title of block 1")).toBeNull();
  });

  it("maps the server's duplicate title to the repeated block", async () => {
    const { user } = await openFacts();
    server.use(
      http.put("*/api/profile/facts", () =>
        HttpResponse.json(
          { detail: "data.blocks: duplicate block title: each must be unique" },
          { status: 422 },
        ),
      ),
    );
    // Titles that differ only by a character the client folds differently.
    await user.click(blockButton(/, block 2$/));
    await user.clear(screen.getByLabelText("Title of block 2"));
    await user.type(screen.getByLabelText("Title of block 2"), "Positioning");
    await user.click(save());
    expect(
      await screen.findByText("Duplicate block title: each must be unique."),
    ).toBeVisible();
  });

  it("starts empty for a new user", async () => {
    const { user, state } = await openFacts(null);
    expect(screen.getByText("No fact block yet.")).toBeVisible();
    expect(screen.getByText("0 bytes of 200 KB")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Add block" }));
    await user.type(screen.getByLabelText("Title of block 1"), "Rules");
    await user.type(screen.getByLabelText("Text of block 1"), "No metric.");
    await user.click(save());
    await screen.findByText("Facts saved.");
    expect(state.saves[0]!.body.base_revision).toBe(0);
    expect(
      await screen.findByRole("tab", { name: /^Facts, 100% complete$/ }),
    ).toBeVisible();
  });

  it("shows the conflict dialog on a stale save and reloads the other version", async () => {
    const { user, state } = await openFacts();
    state.sections.facts = { blocks: [{ title: "Elsewhere", body: "Saved." }] };
    state.revision = 11;
    await user.click(screen.getByRole("button", { name: "Remove block 1" }));
    await user.click(save());
    const dialog = await screen.findByRole("alertdialog", {
      name: "This section changed elsewhere",
    });
    await user.click(
      within(dialog).getByRole("button", { name: "Reload (discard mine)" }),
    );
    expect(
      await screen.findByRole("button", { name: /Elsewhere, block 1/ }),
    ).toBeVisible();
    expect(screen.queryByText("POSITIONING")).toBeNull();
    expect(screen.queryByText("Unsaved changes")).toBeNull();
  });

  it("asks before leaving the tab with unsaved blocks", async () => {
    const { user } = await openFacts();
    await user.click(screen.getByRole("button", { name: "Add block" }));
    await user.click(screen.getByRole("tab", { name: /^Skills/ }));
    const guard = await screen.findByRole("alertdialog", {
      name: "Discard unsaved changes?",
    });
    await user.click(
      within(guard).getByRole("button", { name: "Keep editing" }),
    );
    expect(screen.getByLabelText("Title of block 3")).toBeVisible();
  });
});
