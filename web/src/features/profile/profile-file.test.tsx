import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { toast } from "sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { admin, fakeSession, member } from "@/test/auth";
import { fakeProfile, haraldLike, type FakeProfile } from "@/test/profile";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

afterEach(() => {
  toast.dismiss();
  vi.restoreAllMocks();
});

async function openPage(path = "/profile") {
  const user = userEvent.setup();
  renderApp(path);
  await screen.findByRole("heading", { level: 1, name: "Profile" });
  return user;
}

async function openMenu(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Export or import" }));
  return screen.findByRole("menu");
}

describe("export", () => {
  let blobs: Blob[];
  let downloads: { href: string; download: string }[];
  beforeEach(() => {
    blobs = [];
    downloads = [];
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      writable: true,
      value: (blob: Blob) => {
        blobs.push(blob);
        return `blob:test/${String(blobs.length)}`;
      },
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      writable: true,
      value: () => {},
    });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      downloads.push({ href: this.href, download: this.download });
    });
  });

  it("downloads the export as profile-YYYY-MM-DD.json", async () => {
    fakeSession(member);
    const state = fakeProfile();
    const user = await openPage();
    const menu = await openMenu(user);
    await user.click(
      within(menu).getByRole("menuitem", { name: "Export profile" }),
    );
    expect(
      await screen.findByText("Profile exported as profile-2026-10-09.json."),
    ).toBeVisible();
    expect(state.exports).toBe(1);
    expect(downloads).toEqual([
      { href: "blob:test/1", download: "profile-2026-10-09.json" },
    ]);
    expect(blobs[0]?.type).toBe("application/json");
    const file = JSON.parse(await blobs[0]!.text()) as {
      format: string;
      version: number;
      sections: Record<string, unknown>;
    };
    expect(file.format).toBe("jobapp-profile");
    expect(file.version).toBe(1);
    expect(file.sections.identity).toEqual(haraldLike().identity);
    // The link is gone from the page once clicked.
    expect(document.querySelector("a[download]")).toBeNull();
  });

  it("exports a never saved profile with its empty sections", async () => {
    fakeSession(member);
    fakeProfile(null);
    const user = await openPage();
    await user.click(
      within(await openMenu(user)).getByRole("menuitem", {
        name: "Export profile",
      }),
    );
    await screen.findByText(/Profile exported/);
    const file = JSON.parse(await blobs[0]!.text()) as {
      sections: Record<string, unknown>;
    };
    expect(Object.keys(file.sections)).toEqual([
      "identity",
      "experience",
      "facts",
      "skills",
      "search",
      "answers",
    ]);
    expect(file.sections.facts).toEqual({ blocks: [] });
  });

  it("reports a failed export and downloads nothing", async () => {
    fakeSession(member);
    fakeProfile();
    server.use(
      http.get("*/api/profile/export", () =>
        HttpResponse.json({ detail: "Boom." }, { status: 500 }),
      ),
    );
    const user = await openPage();
    await user.click(
      within(await openMenu(user)).getByRole("menuitem", {
        name: "Export profile",
      }),
    );
    expect(
      await screen.findByText(
        "The profile was not exported. The API answered 500: Boom..",
      ),
    ).toBeVisible();
    expect(downloads).toEqual([]);
  });
});

const exportFile = (
  overrides: Record<string, unknown> = {},
  name = "profile-2026-10-09.json",
) =>
  new File(
    [
      JSON.stringify({
        format: "jobapp-profile",
        version: 1,
        exported_at: "2026-10-09T08:00:00+00:00",
        sections: haraldLike(),
        ...overrides,
      }),
    ],
    name,
    { type: "application/json" },
  );

async function openImport(path = "/profile") {
  const user = await openPage(path);
  await user.click(
    within(await openMenu(user)).getByRole("menuitem", {
      name: "Import profile",
    }),
  );
  const dialog = await screen.findByRole("dialog", { name: "Import profile" });
  return { user, dialog };
}

const replaceButton = (dialog: HTMLElement) =>
  within(dialog).getByRole("button", { name: "Replace my profile" });

describe("import dialog: files refused in the browser", () => {
  it.each([
    [
      "bad JSON",
      new File(["{ not json"], "broken.json", { type: "application/json" }),
      "The file is not valid JSON.",
    ],
    [
      "an empty file",
      new File([], "empty.json", { type: "application/json" }),
      "The file is empty.",
    ],
    [
      "a wrong format",
      exportFile({ format: "linkedin-export" }),
      'Unknown file format: expected "format": "jobapp-profile", a profile export.',
    ],
    [
      "a wrong version",
      exportFile({ version: 0 }),
      "Unsupported export version: this app reads version 1.",
    ],
    [
      "a newer version",
      exportFile({ version: 3 }),
      "Unsupported export version 3: this app reads version 1. The file comes from a newer version of the app.",
    ],
    [
      "a file over 2 MB",
      new File(["x".repeat(2 * 1024 * 1024 + 1)], "huge.json", {
        type: "application/json",
      }),
      "The file is 2.0 MB; an import file is at most 2 MB.",
    ],
  ])("%s", async (_name, file, error) => {
    fakeSession(member);
    const state = fakeProfile();
    const { user, dialog } = await openImport();
    await user.upload(within(dialog).getByLabelText("Profile file"), file);
    expect(
      await within(dialog).findByText("This file cannot be imported"),
    ).toBeVisible();
    expect(within(dialog).getByText(error)).toBeVisible();
    expect(replaceButton(dialog)).toBeDisabled();
    expect(state.imports).toEqual([]);
  });
});

describe("import dialog: a valid file", () => {
  function changedFile() {
    const sections = haraldLike();
    sections.identity.headline = "Imported headline";
    sections.facts.blocks.push({ title: "NEW", body: "Imported block." });
    return exportFile({ sections, base_revision: 1 });
  }

  it("summarises what it replaces, then imports with the page's revision", async () => {
    fakeSession(admin);
    const state = fakeProfile();
    const { user, dialog } = await openImport();
    await user.upload(
      within(dialog).getByLabelText("Profile file"),
      changedFile(),
    );
    const summary = await within(dialog).findByRole("region", {
      name: "What the import replaces",
    });
    const items = within(summary).getAllByRole("listitem");
    expect(items.map((li) => li.textContent)).toEqual([
      "Identity: Harald Example, 3 languagesReplaced",
      "Experience: 3 employers, 2 schoolsUnchanged",
      "Facts: 3 blocksReplaced",
      "Skills: 4 categories, 1 gateUnchanged",
      "Search: 2 boards, 1 enabledUnchanged",
      "Answers: 2 countriesUnchanged",
      "Preferences: Documents: language auto, tone plainUnchanged",
      "Photo: none in the fileKept",
    ]);
    expect(summary).toHaveTextContent(
      "Completeness after the import: 97% (now 97%)",
    );
    expect(summary).toHaveTextContent(/Exported 9 Oct 2026/);
    await user.click(replaceButton(dialog));
    expect(await screen.findByText("Profile imported.")).toBeVisible();
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
    expect(state.imports).toHaveLength(1);
    // The base revision is the page's, never the one in the file.
    expect(state.imports[0]!.base_revision).toBe(6);
    expect(state.revision).toBe(12);
    // The open tab shows the imported data.
    expect(screen.getByLabelText("Headline")).toHaveValue("Imported headline");
  });

  it("shows the completeness the import leads to, and the photo it brings", async () => {
    fakeSession(member);
    fakeProfile(null);
    const { user, dialog } = await openImport();
    const sections = haraldLike();
    sections.answers.motivation = null;
    sections.search.boards = [];
    await user.upload(
      within(dialog).getByLabelText("Profile file"),
      exportFile({
        sections,
        photo: { mime: "image/png", base64: "iVBORw0KGgo=" },
      }),
    );
    const summary = await within(dialog).findByRole("region", {
      name: "What the import replaces",
    });
    // answers 80, search 80, the rest 100: (4 x 100 + 160) / 6 = 93.3
    expect(summary).toHaveTextContent(
      "Completeness after the import: 93% (now 0%)",
    );
    expect(summary).toHaveTextContent("Photo: the file has oneReplaced");
  });

  it("warns that unsaved changes of the open tab will be lost", async () => {
    fakeSession(admin);
    fakeProfile();
    const user = await openPage();
    const field = screen.getByLabelText("Headline");
    await user.clear(field);
    await user.type(field, "Typed");
    await user.click(
      within(await openMenu(user)).getByRole("menuitem", {
        name: "Import profile",
      }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Import profile",
    });
    await user.upload(
      within(dialog).getByLabelText("Profile file"),
      changedFile(),
    );
    expect(
      await within(dialog).findByText(
        "Your unsaved changes to Identity will be lost.",
      ),
    ).toBeVisible();
    await user.click(replaceButton(dialog));
    await screen.findByText("Profile imported.");
    expect(screen.getByLabelText("Headline")).toHaveValue("Imported headline");
    expect(screen.queryByText("Unsaved changes")).toBeNull();
  });

  it("cancel sends nothing", async () => {
    fakeSession(admin);
    const state = fakeProfile();
    const { user, dialog } = await openImport();
    await user.upload(
      within(dialog).getByLabelText("Profile file"),
      changedFile(),
    );
    await within(dialog).findByRole("region");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
    expect(state.imports).toEqual([]);
  });

  it("on a 409, compares with the newer version and imports anyway on request", async () => {
    fakeSession(admin);
    const state: FakeProfile = fakeProfile();
    const { user, dialog } = await openImport();
    await user.upload(
      within(dialog).getByLabelText("Profile file"),
      changedFile(),
    );
    await within(dialog).findByRole("region");
    // Another tab saves the experience meanwhile.
    state.revision += 1;
    state.sections.experience = { employers: [], education: [] };
    await user.click(replaceButton(dialog));
    expect(
      await within(dialog).findByText("Your profile changed elsewhere"),
    ).toBeVisible();
    // The summary now compares with the newer save.
    expect(
      within(dialog).getByText("Experience").closest("li"),
    ).toHaveTextContent("Replaced");
    await user.click(
      within(dialog).getByRole("button", { name: "Import anyway" }),
    );
    await screen.findByText("Profile imported.");
    expect(state.imports.map((body) => body.base_revision)).toEqual([6, 7]);
  });

  it.each([
    [
      413,
      "The import file is larger than 2 MB.",
      "The server refused the file: The import file is larger than 2 MB.",
    ],
    [
      422,
      "sections.skills.have.0.terms: term 1: invalid regular expression at position 0: inline flags such as (?i) are not allowed.",
      "The server refused the file: sections.skills.have.0.terms: term 1: invalid regular expression at position 0: inline flags such as (?i) are not allowed.",
    ],
    [
      500,
      "Boom.",
      "The profile was not imported. The API answered 500: Boom..",
    ],
  ])("shows a %i from the server", async (status, detail, shown) => {
    fakeSession(admin);
    fakeProfile();
    server.use(
      http.post("*/api/profile/import", () =>
        HttpResponse.json({ detail }, { status }),
      ),
    );
    const { user, dialog } = await openImport();
    await user.upload(
      within(dialog).getByLabelText("Profile file"),
      changedFile(),
    );
    await within(dialog).findByRole("region");
    await user.click(replaceButton(dialog));
    expect(await within(dialog).findByText(shown)).toBeVisible();
    // Still open, the same file can be sent again.
    expect(replaceButton(dialog)).toBeEnabled();
  });

  it("reads the same file again when it is chosen a second time", async () => {
    fakeSession(admin);
    fakeProfile();
    const { user, dialog } = await openImport();
    const input =
      within(dialog).getByLabelText<HTMLInputElement>("Profile file");
    const file = changedFile();
    await user.upload(input, file);
    await within(dialog).findByRole("region");
    // The input is emptied after each read, so the browser fires a change for the same file.
    expect(input.value).toBe("");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await user.click(
      within(await openMenu(user)).getByRole("menuitem", {
        name: "Import profile",
      }),
    );
    const again = await screen.findByRole("dialog", { name: "Import profile" });
    await user.upload(within(again).getByLabelText("Profile file"), file);
    expect(await within(again).findByRole("region")).toBeVisible();
  });

  it("choosing another file replaces the error with a summary", async () => {
    fakeSession(admin);
    fakeProfile();
    const { user, dialog } = await openImport();
    const input = within(dialog).getByLabelText("Profile file");
    await user.upload(input, exportFile({ version: 2 }));
    await within(dialog).findByText("This file cannot be imported");
    await user.upload(input, changedFile());
    expect(
      await within(dialog).findByRole("region", {
        name: "What the import replaces",
      }),
    ).toBeVisible();
    expect(
      within(dialog).queryByText("This file cannot be imported"),
    ).toBeNull();
    expect(replaceButton(dialog)).toBeEnabled();
  });
});
