import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { toast } from "sonner";
import { afterEach, describe, expect, it } from "vitest";

import { fakeSession, member } from "@/test/auth";
import { fakeCv, fakeDocuments, fakeDocumentOf } from "@/test/documents";
import { fakeProfile } from "@/test/profile";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

afterEach(() => {
  toast.dismiss();
});

/** A separator dash: an em dash, an en dash, or a hyphen between spaces. */
const SEPARATOR = /[–—]| - /;

async function openCv(path: string) {
  const user = userEvent.setup();
  const app = renderApp(path);
  await screen.findByRole("heading", { level: 1 });
  return { user, ...app };
}

function setup(options: Parameters<typeof fakeDocuments>[0] = {}) {
  fakeSession(member);
  fakeProfile();
  return fakeDocuments(options);
}

describe("the page of one CV", () => {
  it("opens from the list of the profile, below it, with its name and actions", async () => {
    const api = setup({
      cvs: [
        fakeCv({ id: "a", name: "Main CV" }),
        fakeCv({ id: "b", name: "Other", is_default: false }),
      ],
      consent: { accepted: true },
    });
    api.documents.set(
      "a",
      fakeDocumentOf(fakeCv({ id: "a", name: "Main CV" })),
    );
    const { user, location } = await openCv("/profile");
    await user.click(await screen.findByRole("link", { name: "Main CV" }));
    expect(location()).toBe("/profile/cv/a");
    expect(
      await screen.findByRole("heading", { level: 1, name: "Main CV" }),
    ).toBeVisible();
    expect(document.title).toBe("Main CV · jobapp");
    const main = screen.getByRole("main");
    expect(within(main).getByRole("link", { name: "Profile" })).toHaveAttribute(
      "href",
      "/profile#your-cv",
    );
    expect(within(main).getByText("Default")).toBeVisible();
    expect(within(main).getByText("From your profile")).toBeVisible();
    expect(
      await screen.findByRole("img", { name: "Page 1 of version 1" }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Rename Main CV" }),
    ).toBeVisible();
    // The default CV is not offered to be made the default.
    expect(
      screen.queryByRole("button", { name: "Make default for Main CV" }),
    ).toBeNull();
    expect(document.body.textContent).not.toMatch(SEPARATOR);
  });

  it("makes another CV the default and renames it from its page", async () => {
    const api = setup({
      cvs: [
        fakeCv({ id: "a", name: "Main CV" }),
        fakeCv({ id: "b", name: "Other", is_default: false }),
      ],
      consent: { accepted: true },
    });
    const { user } = await openCv("/profile/cv/b");
    await user.click(
      screen.getByRole("button", { name: "Make default for Other" }),
    );
    await waitFor(() => {
      expect(
        api.requests.some(
          (one) =>
            one.method === "PATCH" &&
            one.path === "/api/documents/base-cvs/b" &&
            (one.body as { default?: boolean }).default === true,
        ),
      ).toBe(true);
    });
    await user.click(screen.getByRole("button", { name: "Rename Other" }));
    const dialog = await screen.findByRole("dialog", {
      name: "Rename this CV",
    });
    const name = within(dialog).getByLabelText("Name");
    await user.clear(name);
    await user.type(name, "Paris CV");
    await user.click(within(dialog).getByRole("button", { name: "Save name" }));
    expect(
      await screen.findByRole("heading", { level: 1, name: "Paris CV" }),
    ).toBeVisible();
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
    await openCv("/profile/cv/a");
    expect(
      await screen.findByText("Your first CV is being built from your profile"),
    ).toBeVisible();
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("answers a CV that does not exist", async () => {
    setup({ cvs: [fakeCv({ id: "a" })], consent: { accepted: true } });
    await openCv("/profile/cv/nope");
    expect(
      await screen.findByRole("heading", { name: "This CV does not exist" }),
    ).toBeVisible();
    expect(screen.getByRole("link", { name: "All my CVs" })).toHaveAttribute(
      "href",
      "/profile#your-cv",
    );
  });

  it("deletes the CV it shows from More actions and goes back to the list", async () => {
    setup({
      cvs: [
        fakeCv({ id: "a", name: "Main CV" }),
        fakeCv({ id: "b", name: "Other", is_default: false }),
      ],
      consent: { accepted: true },
    });
    const { user, location } = await openCv("/profile/cv/b");
    await user.click(screen.getByRole("button", { name: "More actions" }));
    await user.click(
      await screen.findByRole("menuitem", { name: "Delete this CV" }),
    );
    const dialog = await screen.findByRole("alertdialog", {
      name: "Delete Other?",
    });
    await user.click(
      within(dialog).getByRole("button", { name: "Delete this CV" }),
    );
    await waitFor(() => {
      expect(location()).toBe("/profile#your-cv");
    });
    expect(await screen.findByRole("link", { name: "Main CV" })).toBeVisible();
    expect(screen.queryByRole("link", { name: "Other" })).toBeNull();
  });

  it("says when the CVs could not be loaded", async () => {
    setup();
    server.use(
      http.get("*/api/documents/base-cvs", () =>
        HttpResponse.json({ detail: "Boom." }, { status: 500 }),
      ),
    );
    await openCv("/profile/cv/a");
    expect(
      await screen.findByText(/^Your CVs could not be loaded\./),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Retry" })).toBeVisible();
  });
});
