import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { toast } from "sonner";
import { afterEach, describe, expect, it } from "vitest";

import { admin, fakeSession, member } from "@/test/auth";
import {
  fakeProfile,
  haraldLike,
  revisionTime,
  type FakeProfile,
} from "@/test/profile";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import type { SectionName } from "./api";

afterEach(() => {
  toast.dismiss();
});

/** A save made before the page loads (or behind its back, for a 409). */
function pushSave(state: FakeProfile, section: SectionName, data: unknown) {
  state.revision += 1;
  state.sections[section] = structuredClone(data);
  state.revisions.push({
    revision: state.revision,
    section,
    created_at: revisionTime(state.revision),
    data: structuredClone(data),
  });
}

async function openHistory(section = "identity", path?: string) {
  const user = userEvent.setup();
  renderApp(path ?? `/profile?tab=${section}`);
  await screen.findByRole("heading", { level: 1, name: "Profile" });
  await user.click(
    screen.getByRole("button", { name: `History of ${section}` }),
  );
  const drawer = await screen.findByRole("dialog", {
    name: `${section.charAt(0).toUpperCase()}${section.slice(1)} history`,
  });
  return { user, drawer };
}

const versionButton = (drawer: HTMLElement, revision: number) =>
  within(drawer).findByRole("button", {
    name: new RegExp(`^Revision ${String(revision)}\\b`),
  });

describe("history drawer", () => {
  it("says so when a section was never saved", async () => {
    fakeSession(member);
    fakeProfile(null);
    const { drawer } = await openHistory();
    expect(
      await within(drawer).findByText(
        "No saved version of identity yet. Each save adds one here.",
      ),
    ).toBeVisible();
    expect(
      within(drawer).queryByRole("button", { name: "Load more" }),
    ).toBeNull();
  });

  it("describes itself to screen readers", async () => {
    fakeSession(member);
    fakeProfile(null);
    const { drawer } = await openHistory();
    expect(drawer).toHaveAccessibleDescription(
      "Every save of identity is kept. Choose one to compare it with the current version and restore it.",
    );
  });

  it("lists one version as current, with relative and absolute dates, and cannot restore it", async () => {
    fakeSession(member);
    const state = fakeProfile(null);
    pushSave(state, "identity", haraldLike().identity);
    const { user, drawer } = await openHistory();
    const item = await versionButton(drawer, 1);
    expect(item).toHaveTextContent("Current");
    const time = item.querySelector("time");
    expect(time).toHaveAttribute("dateTime", revisionTime(1));
    expect(time?.textContent).toMatch(/ago$|^last |^yesterday$/);
    expect(item).toHaveTextContent(/1 Oct 2026/);
    await user.click(item);
    expect(item).toHaveAttribute("aria-current", "true");
    expect(
      await within(drawer).findByText("This is the current version."),
    ).toBeVisible();
    expect(
      within(drawer).getByRole("button", { name: "Restore this version" }),
    ).toBeDisabled();
    expect(
      within(drawer).getByText("No difference with the current version."),
    ).toBeVisible();
  });

  it("only lists the saves of its own section", async () => {
    fakeSession(admin);
    fakeProfile();
    const { drawer } = await openHistory("skills");
    const list = await within(drawer).findByRole("navigation", {
      name: "Versions of skills",
    });
    await within(list).findByRole("button", { name: /^Revision 4/ });
    expect(within(list).getAllByRole("button")).toHaveLength(1);
  });

  it("pages back 20 at a time with Load more", async () => {
    fakeSession(member);
    const state = fakeProfile(null);
    const requests: string[] = [];
    server.events.on("request:start", ({ request }) => {
      if (request.url.includes("/api/profile/revisions?"))
        requests.push(new URL(request.url).search);
    });
    for (let i = 0; i < 45; i += 1)
      pushSave(state, "identity", {
        ...haraldLike().identity,
        headline: `H${String(i)}`,
      });
    const { user, drawer } = await openHistory();
    await versionButton(drawer, 45);
    const nav = within(drawer).getByRole("navigation");
    expect(within(nav).getAllByRole("listitem")).toHaveLength(20);
    await user.click(within(drawer).getByRole("button", { name: "Load more" }));
    await versionButton(drawer, 6);
    expect(within(nav).getAllByRole("listitem")).toHaveLength(40);
    await user.click(within(drawer).getByRole("button", { name: "Load more" }));
    await versionButton(drawer, 1);
    expect(within(nav).getAllByRole("listitem")).toHaveLength(45);
    expect(
      within(drawer).queryByRole("button", { name: "Load more" }),
    ).toBeNull();
    expect(requests).toEqual([
      "?section=identity&limit=20",
      "?section=identity&limit=20&before=26",
      "?section=identity&limit=20&before=6",
    ]);
    server.events.removeAllListeners();
  });

  it("shows a failed list and retries", async () => {
    fakeSession(admin);
    fakeProfile();
    let fail = true;
    server.use(
      http.get("*/api/profile/revisions", () =>
        fail
          ? HttpResponse.json({ detail: "Boom." }, { status: 500 })
          : undefined,
      ),
    );
    const { user, drawer } = await openHistory();
    expect(
      await within(drawer).findByText(/The versions could not be loaded/),
    ).toBeVisible();
    fail = false;
    await user.click(within(drawer).getByRole("button", { name: "Retry" }));
    expect(await versionButton(drawer, 1)).toBeVisible();
  });

  it("shows a revision that cannot be loaded", async () => {
    fakeSession(admin);
    fakeProfile();
    server.use(
      http.get("*/api/profile/revisions/:revision", () =>
        HttpResponse.json({ detail: "Not found." }, { status: 404 }),
      ),
    );
    const { user, drawer } = await openHistory();
    await user.click(await versionButton(drawer, 1));
    expect(
      await within(drawer).findByText("Revision 1 could not be loaded"),
    ).toBeVisible();
  });
});

describe("restore", () => {
  function twoIdentitySaves() {
    const state = fakeProfile();
    const first = { ...haraldLike().identity, headline: "Applied AI Engineer" };
    const second = { ...first, headline: "Staff Engineer", city: "Paris" };
    pushSave(state, "identity", first); // 7
    pushSave(state, "identity", second); // 8
    return state;
  }

  it("saves the chosen version as a new revision, reloads the tab and the list", async () => {
    fakeSession(admin);
    const state = twoIdentitySaves();
    const { user, drawer } = await openHistory();
    expect(screen.getByLabelText("Headline")).toHaveValue("Staff Engineer");
    await user.click(await versionButton(drawer, 7));
    const table = await within(drawer).findByRole("table", {
      name: "Revision 7 and the current version",
    });
    expect(
      within(drawer).getByText("2 fields differ from the current version."),
    ).toBeVisible();
    const headline = within(table).getByRole("row", { name: /^Headline/ });
    expect(headline).toHaveAttribute("data-status", "changed");
    expect(headline).toHaveTextContent("Changed");
    expect(headline).toHaveTextContent("Applied AI Engineer");
    expect(headline).toHaveTextContent("Staff Engineer");
    expect(
      within(table).getByRole("row", { name: /^Full name/ }),
    ).toHaveAttribute("data-status", "same");

    await user.click(
      within(drawer).getByRole("button", { name: "Restore this version" }),
    );
    expect(
      await screen.findByText("Revision 7 of identity restored as revision 9."),
    ).toBeVisible();
    const put = state.saves.at(-1)!;
    expect(put.section).toBe("identity");
    expect(put.body.base_revision).toBe(8);
    expect(put.body.data).toEqual(state.revisions[6]!.data);
    // The tab shows the restored version, nothing left unsaved.
    expect(screen.getByLabelText("Headline")).toHaveValue(
      "Applied AI Engineer",
    );
    expect(screen.getByLabelText("City")).toHaveValue("Bordeaux");
    expect(screen.queryByText("Unsaved changes")).toBeNull();
    // The list shows the new current version.
    expect(await versionButton(drawer, 9)).toHaveTextContent("Current");
  });

  it("bases a save made after a restore on the new revision", async () => {
    fakeSession(admin);
    const state = twoIdentitySaves();
    const { user, drawer } = await openHistory();
    await user.click(await versionButton(drawer, 7));
    await user.click(
      await within(drawer).findByRole("button", {
        name: "Restore this version",
      }),
    );
    await screen.findByText(/restored as revision 9/);
    await user.keyboard("{Escape}");
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
    const field = screen.getByLabelText("Headline");
    await user.clear(field);
    await user.type(field, "After restore");
    await user.click(screen.getByRole("button", { name: "Save identity" }));
    expect(await screen.findByText("Identity saved.")).toBeVisible();
    expect(state.saves.at(-1)!.body.base_revision).toBe(9);
  });

  it("asks on a 409, then restores over the newer save", async () => {
    fakeSession(admin);
    const state = twoIdentitySaves();
    const { user, drawer } = await openHistory();
    await user.click(await versionButton(drawer, 7));
    await within(drawer).findByRole("table");
    // Another tab saves the answers meanwhile.
    pushSave(state, "answers", {
      ...haraldLike().answers,
      notice_period: "Two weeks.",
    }); // 9
    await user.click(
      within(drawer).getByRole("button", { name: "Restore this version" }),
    );
    const dialog = await screen.findByRole("alertdialog", {
      name: "Your profile changed elsewhere",
    });
    expect(dialog).toHaveTextContent("Restore revision 7 over it anyway");
    expect(state.saves.at(-1)!.body.base_revision).toBe(8);
    await user.click(
      within(dialog).getByRole("button", { name: "Restore anyway" }),
    );
    expect(
      await screen.findByText(
        "Revision 7 of identity restored as revision 10.",
      ),
    ).toBeVisible();
    expect(state.saves.at(-1)!.body.base_revision).toBe(9);
    expect(state.sections.answers).toMatchObject({
      notice_period: "Two weeks.",
    });
  });

  it("cancelling after a 409 restores nothing", async () => {
    fakeSession(admin);
    const state = twoIdentitySaves();
    const { user, drawer } = await openHistory();
    await user.click(await versionButton(drawer, 7));
    await within(drawer).findByRole("table");
    pushSave(state, "identity", {
      ...haraldLike().identity,
      headline: "From another tab",
    }); // 9
    await user.click(
      within(drawer).getByRole("button", { name: "Restore this version" }),
    );
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(state.revision).toBe(9);
    // The tab now shows the save from the other tab, and the comparison too.
    expect(screen.getByLabelText("Headline")).toHaveValue("From another tab");
    const headline = within(within(drawer).getByRole("table")).getByRole(
      "row",
      { name: /^Headline/ },
    );
    expect(headline).toHaveTextContent("From another tab");
  });

  it("shows the server's refusal of an old version", async () => {
    fakeSession(admin);
    twoIdentitySaves();
    server.use(
      http.put("*/api/profile/identity", () =>
        HttpResponse.json(
          { detail: "data.email: value is not a valid email address" },
          { status: 422 },
        ),
      ),
    );
    const { user, drawer } = await openHistory();
    await user.click(await versionButton(drawer, 7));
    await user.click(
      await within(drawer).findByRole("button", {
        name: "Restore this version",
      }),
    );
    expect(
      await within(drawer).findByText(
        "This version cannot be restored as it is: data.email: value is not a valid email address",
      ),
    ).toBeVisible();
  });

  it("guards unsaved changes: keep editing sends nothing, discard restores", async () => {
    fakeSession(admin);
    const state = twoIdentitySaves();
    const user = userEvent.setup();
    renderApp("/profile?tab=identity");
    await screen.findByRole("heading", { level: 1, name: "Profile" });
    const field = screen.getByLabelText("Headline");
    await user.clear(field);
    await user.type(field, "Typed but not saved");
    expect(screen.getByText("Unsaved changes")).toBeVisible();
    await user.click(
      screen.getByRole("button", { name: "History of identity" }),
    );
    const drawer = await screen.findByRole("dialog", {
      name: "Identity history",
    });
    await user.click(await versionButton(drawer, 7));
    await user.click(
      await within(drawer).findByRole("button", {
        name: "Restore this version",
      }),
    );
    let guard = await screen.findByRole("alertdialog", {
      name: "Discard unsaved changes?",
    });
    expect(guard).toHaveTextContent(
      "Your changes to Identity are not saved. Restoring revision 7 replaces them.",
    );
    const savesBefore = state.saves.length;
    await user.click(
      within(guard).getByRole("button", { name: "Keep editing" }),
    );
    expect(state.saves).toHaveLength(savesBefore);
    await user.click(
      within(drawer).getByRole("button", { name: "Restore this version" }),
    );
    guard = await screen.findByRole("alertdialog", {
      name: "Discard unsaved changes?",
    });
    await user.click(
      within(guard).getByRole("button", { name: "Discard and restore" }),
    );
    await screen.findByText(/restored as revision 9/);
    expect(screen.getByLabelText("Headline")).toHaveValue(
      "Applied AI Engineer",
    );
    expect(screen.queryByText("Unsaved changes")).toBeNull();
  });

  it("an older version identical to the current one has nothing to restore", async () => {
    fakeSession(admin);
    const state = fakeProfile();
    pushSave(state, "identity", haraldLike().identity); // 7, same data as 1
    const { user, drawer } = await openHistory();
    await user.click(await versionButton(drawer, 1));
    expect(
      await within(drawer).findByText(
        "This version holds the same data as the current one.",
      ),
    ).toBeVisible();
    expect(
      within(drawer).getByRole("button", { name: "Restore this version" }),
    ).toBeDisabled();
  });
});

describe("diff rendering per section", () => {
  type Change = (state: FakeProfile) => void;
  const cases: Array<
    [SectionName, Change, string, RegExp, string | undefined]
  > = [
    [
      "experience",
      (state) => {
        const data = haraldLike().experience;
        data.employers.pop();
        pushSave(state, "experience", data);
      },
      "Employer 3 › Organisation",
      /^Employer 3 › Organisation/,
      "added",
    ],
    [
      "skills",
      (state) => {
        const data = haraldLike().skills;
        data.have[0]!.terms = ["agent"];
        pushSave(state, "skills", data);
      },
      "Have 1 › Terms",
      /^Have 1 › Terms/,
      "changed",
    ],
    [
      "search",
      (state) => {
        const data = haraldLike().search;
        data.boards[1]!.enabled = true;
        data.min_fit = 80;
        pushSave(state, "search", data);
      },
      "Board 2 › Enabled",
      /^Board 2 › Enabled/,
      "changed",
    ],
    [
      "answers",
      (state) => {
        const data = haraldLike().answers;
        data.motivation = "A new motivation.";
        pushSave(state, "answers", data);
      },
      "Motivation",
      /^Motivation/,
      "changed",
    ],
  ];

  it.each(cases)(
    "%s highlights the changed fields",
    async (section, change, _label, row, status) => {
      fakeSession(admin);
      const state = fakeProfile();
      const older = state.revisions.find((r) => r.section === section)!;
      change(state);
      const { user, drawer } = await openHistory(section);
      await user.click(await versionButton(drawer, older.revision));
      const table = await within(drawer).findByRole("table");
      expect(within(table).getByRole("row", { name: row })).toHaveAttribute(
        "data-status",
        status,
      );
      const changed = table.querySelectorAll(
        'tr[data-status="changed"], tr[data-status="added"], tr[data-status="removed"]',
      );
      expect(changed.length).toBeGreaterThan(0);
      // Only differences hides the rest.
      await user.click(within(drawer).getByLabelText("Only differences"));
      expect(within(table).getAllByRole("row")).toHaveLength(
        changed.length + 1,
      );
    },
  );

  it("experience: a field only the current version has is marked as such", async () => {
    fakeSession(admin);
    const state = fakeProfile();
    const data = haraldLike().experience;
    data.education.push({
      school: "New School",
      degree: "MBA",
      start: null,
      end: null,
      location: null,
      details: null,
    });
    pushSave(state, "experience", data);
    const { user, drawer } = await openHistory("experience");
    await user.click(await versionButton(drawer, 2));
    const row = within(await within(drawer).findByRole("table")).getByRole(
      "row",
      { name: /^School 3 › School/ },
    );
    expect(row).toHaveAttribute("data-status", "removed");
    expect(row).toHaveTextContent("Only in the current version");
    expect(row).toHaveTextContent("Empty");
  });

  it("experience: a year alone made a month is one changed field, each shown as stored", async () => {
    fakeSession(admin);
    const years = haraldLike().experience;
    years.employers[2] = { ...years.employers[2]!, start: "2021", end: "2023" };
    const state = fakeProfile({ ...haraldLike(), experience: years });
    const months = structuredClone(years);
    months.employers[2]!.start = "2021-03";
    pushSave(state, "experience", months);
    const { user, drawer } = await openHistory("experience");
    await user.click(await versionButton(drawer, 2));
    const table = await within(drawer).findByRole("table");
    expect(
      within(drawer).getByText("1 field differs from the current version."),
    ).toBeVisible();
    const start = within(table).getByRole("row", {
      name: /^Employer 3 › Start/,
    });
    expect(start).toHaveAttribute("data-status", "changed");
    expect(
      within(start)
        .getAllByRole("cell")
        .map((cell) => cell.textContent),
    ).toEqual(["2021", "2021-03"]);
    // The end, a year alone on both sides, is the same, and shown as the year.
    const end = within(table).getByRole("row", { name: /^Employer 3 › End/ });
    expect(end).toHaveAttribute("data-status", "same");
    expect(end).toHaveTextContent("2023");
    expect(end).not.toHaveTextContent("2023-");
  });

  it("facts: added and removed lines of the Markdown", async () => {
    fakeSession(admin);
    const state = fakeProfile();
    const data = haraldLike().facts;
    data.blocks[0]!.body =
      "1. Every claim traces to this file.\n3. A new rule.";
    pushSave(state, "facts", data);
    const { user, drawer } = await openHistory("facts");
    await user.click(await versionButton(drawer, 3));
    const lines = await within(drawer).findByRole("list", {
      name: "Lines of the fact base",
    });
    const added = lines.querySelectorAll('[data-kind="added"]');
    const removed = lines.querySelectorAll('[data-kind="removed"]');
    expect([...added].map((li) => li.textContent)).toEqual([
      "Only in revision 3: 2. No invented metric.",
    ]);
    expect([...removed].map((li) => li.textContent)).toEqual([
      "Only in the current version: 3. A new rule.",
    ]);
    expect(
      within(drawer).getByText(
        "1 line only in revision 3, 1 only in the current version.",
      ),
    ).toBeVisible();
    // Markdown and HTML in the fact base are shown as text, never rendered.
    expect(lines).toHaveTextContent("<script>");
    expect(lines.querySelector("script")).toBeNull();
  });
});
