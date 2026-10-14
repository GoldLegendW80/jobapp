import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { toast } from "sonner";

import { fakeSession, member } from "@/test/auth";
import {
  CONSENT_TEXT,
  fakeCv,
  fakeDocuments,
  fakeDocumentOf,
} from "@/test/documents";
import { fakeProfile } from "@/test/profile";
import { renderApp } from "@/test/render";

import { POLL_MS } from "./document-view";
import type { BaseCv } from "./documents-api";

afterEach(() => {
  toast.dismiss();
});

/** A separator dash: an em dash, an en dash, or a hyphen between spaces. */
const SEPARATOR = /[–—]| - /;

async function openCvs(path = "/profile/cv") {
  const user = userEvent.setup();
  const app = renderApp(path);
  await screen.findByRole("heading", { level: 1, name: "Profile" });
  return { user, ...app };
}

function setup(options: Parameters<typeof fakeDocuments>[0] = {}) {
  fakeSession(member);
  fakeProfile();
  return fakeDocuments(options);
}

const writes = (api: ReturnType<typeof fakeDocuments>) =>
  api.requests.filter((one) => one.method !== "GET");

describe("the CV tab", () => {
  it("is the first tab of the profile, open at /profile/cv, and the others keep their addresses", async () => {
    setup({ cvs: [fakeCv()] });
    const { user, location } = await openCvs();
    const tabs = screen.getAllByRole("tab");
    expect(tabs[0]).toHaveTextContent("CV");
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
    expect(
      await screen.findByRole("heading", { name: "Your CVs" }),
    ).toBeVisible();

    await user.click(screen.getByRole("tab", { name: /^Skills/ }));
    expect(location()).toBe("/profile?tab=skills");
    await user.click(screen.getByRole("tab", { name: "CV" }));
    expect(location()).toBe("/profile/cv");
    expect(
      await screen.findByRole("heading", { name: "Your CVs" }),
    ).toBeVisible();
  });

  it("shows the empty state and asks for the consent before anything is sent", async () => {
    const api = setup();
    const { user } = await openCvs();
    expect(await screen.findByText("You have no CV yet")).toBeVisible();
    expect(screen.getByTestId("confirm-line")).toHaveTextContent(
      "Confirm what is sent to build your CV.",
    );

    await user.click(
      screen.getByRole("button", { name: "Build it from my profile" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "What is sent to build your CV",
    });
    expect(within(dialog).getByTestId("consent-text")).toHaveTextContent(
      CONSENT_TEXT,
    );
    expect(within(dialog).getByTestId("consent-model")).toHaveTextContent(
      "claude-opus-5-5",
    );
    const estimates = within(dialog).getByTestId("consent-estimates");
    expect(estimates).toHaveTextContent(
      "One posting: about 12,000 to 20,000 tokens.",
    );
    expect(estimates).toHaveTextContent(
      "A typical week at your threshold of 70: 5 postings, about 60,000 to 100,000 tokens.",
    );
    expect(estimates).toHaveTextContent("tokens count against your quota");
    const agree = within(dialog).getByRole("button", {
      name: "Agree and build my CV",
    });
    expect(agree).toBeDisabled();

    // Declined: nothing is recorded, nothing is built, the tab keeps asking.
    await user.click(within(dialog).getByRole("button", { name: "Not now" }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
    expect(writes(api)).toEqual([]);
    expect(screen.getByTestId("confirm-line")).toBeVisible();
  });

  it("records the consent of the version shown, then builds the CV from the profile", async () => {
    const api = setup();
    const { user } = await openCvs();
    await user.click(
      await screen.findByRole("button", { name: "Build it from my profile" }),
    );
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("checkbox"));
    await user.click(
      within(dialog).getByRole("button", { name: "Agree and build my CV" }),
    );

    expect(
      await screen.findByText("Your first CV is being built."),
    ).toBeVisible();
    expect(writes(api).map((one) => [one.method, one.path, one.body])).toEqual([
      ["POST", "/api/documents/consent", { version: 1 }],
      ["POST", "/api/documents/base-cvs", { from: "profile" }],
    ]);
    expect(
      api.requests.every(
        (one) =>
          one.method === "GET" ||
          one.headers.get("x-requested-with") === "jobapp",
      ),
    ).toBe(true);
  });

  it("builds at once when the consent was given, and shows a reload still building", async () => {
    const api = setup({ consent: { accepted: true } });
    const { user } = await openCvs();
    await user.click(
      await screen.findByRole("button", { name: "Build it from my profile" }),
    );
    expect(
      await screen.findByText("Your first CV is being built."),
    ).toBeVisible();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(writes(api).map((one) => one.path)).toEqual([
      "/api/documents/base-cvs",
    ]);
  });

  it("keeps the dialog usable by keyboard alone", async () => {
    setup();
    const { user } = await openCvs();
    const build = await screen.findByRole("button", {
      name: "Build it from my profile",
    });
    build.focus();
    await user.keyboard("{Enter}");
    const dialog = await screen.findByRole("dialog");
    // Tab reaches the box, a space ticks it, and tab then reaches the enabled button.
    const box = within(dialog).getByRole("checkbox");
    for (let stops = 0; stops < 6 && document.activeElement !== box; stops += 1)
      await user.tab();
    expect(box).toHaveFocus();
    await user.keyboard(" ");
    expect(box).toBeChecked();
    const agree = within(dialog).getByRole("button", {
      name: "Agree and build my CV",
    });
    for (
      let stops = 0;
      stops < 3 && document.activeElement !== agree;
      stops += 1
    )
      await user.tab();
    expect(agree).toHaveFocus();
    expect(agree).toBeEnabled();
    await user.keyboard("{Escape}");
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
  });

  it("lists the CVs with their source, template and default, and the threshold line", async () => {
    setup({
      cvs: [
        fakeCv({ id: "a", name: "Main CV" }),
        fakeCv({
          id: "b",
          name: "French CV",
          is_default: false,
          source: "built",
          template_slug: "compact",
        }),
      ],
      consent: { accepted: true, min_fit: 65 },
    });
    await openCvs();
    const list = await screen.findByRole("list", { name: "Your CVs" });
    const [first, second] = within(list).getAllByRole("listitem");
    expect(first).toHaveTextContent("Main CV");
    expect(first).toHaveTextContent("Default");
    expect(first).toHaveTextContent("From your profile");
    expect(first).toHaveTextContent("Template: Classic");
    expect(second).toHaveTextContent("Built in the app");
    expect(second).toHaveTextContent("Template: Compact");
    expect(second).not.toHaveTextContent("Default");
    expect(
      within(first!).queryByRole("button", { name: /Make .* the default/ }),
    ).toBeNull();
    const line = await screen.findByTestId("threshold");
    expect(line).toHaveTextContent(
      "Documents are written for new postings with a fit of 65 or more.",
    );
    expect(within(line).getByRole("link")).toHaveAttribute(
      "href",
      "/profile?tab=search",
    );
  });

  it("makes another CV the default, renames it, and deletes it", async () => {
    const api = setup({
      cvs: [
        fakeCv({ id: "a", name: "Main CV" }),
        fakeCv({ id: "b", name: "Second", is_default: false }),
      ],
      consent: { accepted: true },
    });
    const { user } = await openCvs();
    await user.click(
      await screen.findByRole("button", { name: "Make Second the default" }),
    );
    await waitFor(() => {
      expect(api.cvs.find((one) => one.id === "b")?.is_default).toBe(true);
    });
    expect(api.cvs.find((one) => one.id === "a")?.is_default).toBe(false);
    expect(writes(api)).toContainEqual(
      expect.objectContaining({
        method: "PATCH",
        path: "/api/documents/base-cvs/b",
        body: { default: true },
      }),
    );

    await user.click(screen.getByRole("button", { name: "Rename Second" }));
    const rename = await screen.findByRole("dialog", {
      name: "Rename this CV",
    });
    const input = within(rename).getByLabelText("Name");
    await user.clear(input);
    expect(
      within(rename).getByRole("button", { name: "Save name" }),
    ).toBeDisabled();
    await user.type(input, "CV for Paris");
    await user.click(within(rename).getByRole("button", { name: "Save name" }));
    expect(
      await screen.findByRole("link", { name: "CV for Paris" }),
    ).toBeVisible();

    // Deleting the default says which one takes its place; the posting documents stay.
    await user.click(
      screen.getByRole("button", { name: "Delete CV for Paris" }),
    );
    const confirm = await screen.findByRole("alertdialog");
    expect(confirm).toHaveTextContent(
      "It is the default: the oldest other CV becomes the default.",
    );
    expect(confirm).toHaveTextContent("stay as they are");
    await user.click(
      within(confirm).getByRole("button", { name: "Delete this CV" }),
    );
    await waitFor(() => {
      expect(screen.queryByRole("link", { name: "CV for Paris" })).toBeNull();
    });
    expect(api.cvs.map((one) => [one.id, one.is_default])).toEqual([
      ["a", true],
    ]);
  });

  it("deletes the last CV and goes back to the empty state", async () => {
    setup({
      cvs: [fakeCv({ id: "a", name: "Only CV" })],
      consent: { accepted: true },
    });
    const { user } = await openCvs();
    await user.click(
      await screen.findByRole("button", { name: "Delete Only CV" }),
    );
    const confirm = await screen.findByRole("alertdialog");
    expect(confirm).toHaveTextContent("It is your only CV");
    await user.click(
      within(confirm).getByRole("button", { name: "Delete this CV" }),
    );
    expect(await screen.findByText("You have no CV yet")).toBeVisible();
  });

  it("makes a copy without asking for the consent: a copy sends nothing to a model", async () => {
    const api = setup({ cvs: [fakeCv({ id: "a", name: "Main CV" })] });
    const { user } = await openCvs();
    await user.click(await screen.findByRole("button", { name: "New CV" }));
    const dialog = await screen.findByRole("dialog", { name: "New CV" });
    await user.click(
      within(dialog).getByRole("radio", { name: "A copy of another CV" }),
    );
    await user.type(
      within(dialog).getByLabelText("Name (optional)"),
      "Paris copy",
    );
    await user.click(
      within(dialog).getByRole("button", { name: "Make a copy" }),
    );
    expect(
      await screen.findByRole("link", { name: "Paris copy" }),
    ).toBeVisible();
    expect(writes(api).map((one) => [one.path, one.body])).toEqual([
      [
        "/api/documents/base-cvs",
        { from: "copy", copy_of: "a", name: "Paris copy" },
      ],
    ]);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("starts the consent from New CV, closing the dialog under it", async () => {
    const api = setup({ cvs: [fakeCv({ id: "a" })] });
    const { user } = await openCvs();
    await user.click(await screen.findByRole("button", { name: "New CV" }));
    const dialog = await screen.findByRole("dialog", { name: "New CV" });
    await user.click(within(dialog).getByRole("button", { name: "Build it" }));
    expect(
      await screen.findByRole("dialog", {
        name: "What is sent to build your CV",
      }),
    ).toBeVisible();
    expect(screen.queryByRole("dialog", { name: "New CV" })).toBeNull();
    expect(writes(api)).toEqual([]);
  });

  it("disables New CV at the most, with the sentence", async () => {
    const cvs = Array.from({ length: 10 }, (_, index): BaseCv =>
      fakeCv({
        id: `c${String(index)}`,
        name: `CV ${String(index)}`,
        is_default: index === 0,
      }),
    );
    setup({ cvs, consent: { accepted: true } });
    await openCvs();
    const button = await screen.findByRole("button", { name: "New CV" });
    expect(button).toBeDisabled();
    expect(
      screen.getByText(
        "You have 10 CVs, the most the app keeps. Delete one to add another.",
      ),
    ).toBeVisible();
  });

  it("says why a CV waits and what failed", async () => {
    setup({
      cvs: [
        fakeCv({
          id: "a",
          name: "Waits",
          last_version_at: null,
          generation: {
            id: "g",
            status: "waiting",
            wait_reason: "credential",
            error: null,
          },
        }),
        fakeCv({
          id: "b",
          name: "Broke",
          is_default: false,
          generation: {
            id: "h",
            status: "failed",
            wait_reason: null,
            error: "token_limit",
          },
        }),
      ],
      consent: { accepted: true },
    });
    await openCvs();
    expect(
      await screen.findByText(
        "Waiting for a model credential. Add one in your account.",
      ),
    ).toBeVisible();
    expect(
      screen.getByText(
        "The writer reached its limit before it finished. Try again.",
      ),
    ).toBeVisible();
  });

  it("reads the list again while a CV is being built and shows it done", async () => {
    const api = setup({
      cvs: [
        fakeCv({
          id: "a",
          name: "Building",
          last_version_at: null,
          generation: {
            id: "g",
            status: "running",
            wait_reason: null,
            error: null,
          },
        }),
      ],
      consent: { accepted: true },
    });
    await openCvs();
    expect(
      await screen.findByText("Your first CV is being built."),
    ).toBeVisible();
    api.cvs[0] = fakeCv({ id: "a", name: "Building" });
    await waitFor(
      () => {
        expect(screen.queryByText("Your first CV is being built.")).toBeNull();
      },
      { timeout: POLL_MS * 3 },
    );
    expect(screen.getByText(/Last version/)).toBeVisible();
  });

  it("shows a failed build call as a toast and refreshes the list", async () => {
    const api = setup({
      cvs: Array.from({ length: 10 }, (_, index) =>
        fakeCv({ id: `c${String(index)}`, is_default: index === 0 }),
      ),
      consent: { accepted: true },
      max: 10,
    });
    // Another tab filled the list after this page loaded: the server answers 409.
    api.cvs = api.cvs.slice(0, 9);
    const { user } = await openCvs();
    await screen.findAllByRole("listitem");
    api.cvs.push(fakeCv({ id: "extra", is_default: false }));
    await user.click(screen.getByRole("button", { name: "New CV" }));
    await user.click(
      within(await screen.findByRole("dialog")).getByRole("button", {
        name: "Build it",
      }),
    );
    expect(
      await screen.findByText(
        "You have 10 CVs, the most the app keeps. Delete one to add another.",
        { selector: "[data-sonner-toast] *" },
      ),
    ).toBeVisible();
  });

  it("uses no dash as a separator in any state of the tab", async () => {
    const api = setup({ consent: { accepted: false } });
    const { user } = await openCvs();
    const states: string[] = [];
    const seen = () => states.push(document.body.textContent);
    await screen.findByText("You have no CV yet");
    seen();
    await user.click(
      screen.getByRole("button", { name: "Build it from my profile" }),
    );
    await screen.findByRole("dialog");
    seen();
    await user.click(screen.getByRole("button", { name: "Not now" }));
    api.cvs.push(
      fakeCv({
        id: "w",
        name: "Waits",
        last_version_at: null,
        generation: {
          id: "g",
          status: "waiting",
          wait_reason: "daily_ceiling",
          error: null,
        },
      }),
    );
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
    expect(states.length).toBe(2);
    for (const text of states) expect(text).not.toMatch(SEPARATOR);
  });
});

describe("one CV", () => {
  it("opens the document view of the CV at /profile/cv/:id, with its actions", async () => {
    const api = setup({
      cvs: [fakeCv({ id: "a", name: "Main CV" })],
      consent: { accepted: true },
    });
    api.documents.set(
      "a",
      fakeDocumentOf(fakeCv({ id: "a", name: "Main CV" })),
    );
    const { user, location } = await openCvs();
    await user.click(await screen.findByRole("link", { name: "Main CV" }));
    expect(location()).toBe("/profile/cv/a");
    expect(
      await screen.findByRole("heading", { name: "Main CV" }),
    ).toBeVisible();
    expect(
      await screen.findByRole("img", { name: "Page 1 of version 1" }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Rename Main CV" }),
    ).toBeVisible();
    expect(document.body.textContent).not.toMatch(SEPARATOR);
  });

  it("passes the generation of the list to the view while a CV is being built", async () => {
    const building = fakeCv({
      id: "a",
      last_version_at: null,
      generation: {
        id: "g",
        status: "running",
        wait_reason: null,
        error: null,
      },
    });
    setup({ cvs: [building], consent: { accepted: true } });
    await openCvs("/profile/cv/a");
    expect(
      await screen.findByText("Your first CV is being built from your profile"),
    ).toBeVisible();
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("answers a CV that does not exist", async () => {
    setup({ cvs: [fakeCv({ id: "a" })], consent: { accepted: true } });
    await openCvs("/profile/cv/nope");
    expect(await screen.findByText("This CV does not exist")).toBeVisible();
    expect(screen.getByRole("link", { name: "All my CVs" })).toHaveAttribute(
      "href",
      "/profile/cv",
    );
  });

  it("goes back to the list after deleting the CV it shows", async () => {
    setup({
      cvs: [
        fakeCv({ id: "a", name: "Main CV" }),
        fakeCv({ id: "b", name: "Other", is_default: false }),
      ],
      consent: { accepted: true },
    });
    const { user, location } = await openCvs("/profile/cv/b");
    await user.click(
      await screen.findByRole("button", { name: "Delete Other" }),
    );
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", {
        name: "Delete this CV",
      }),
    );
    await waitFor(() => {
      expect(location()).toBe("/profile/cv");
    });
    expect(await screen.findByRole("link", { name: "Main CV" })).toBeVisible();
  });
});
