import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { toast } from "sonner";
import { afterEach, describe, expect, it, vi } from "vitest";

import { admin, fakeSession, member } from "@/test/auth";
import { fakeAbout } from "@/test/about";
import { fakeDocuments } from "@/test/documents";
import { fakeProfile, haraldLike } from "@/test/profile";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import type {
  Employer,
  Experience,
  Identity,
  Skills,
} from "../api/profile-api";

// Sonner keeps its toasts in a module store: clear them so one test never sees another's.
afterEach(() => {
  toast.dismiss();
  vi.restoreAllMocks();
});

async function openProfile(path = "/profile") {
  const user = userEvent.setup();
  fakeDocuments();
  const app = renderApp(path);
  await screen.findByRole("heading", { level: 1, name: "Profile" });
  return { user, ...app };
}

const saveButton = (section: string) =>
  screen.getByRole("button", { name: `Save ${section}` });
const rail = () => screen.getByRole("region", { name: "You" });

describe("profile page", () => {
  it("is linked from the shell for every signed in user", async () => {
    fakeSession(member);
    fakeProfile();
    const user = userEvent.setup();
    renderApp("/");
    const nav = await screen.findByRole("navigation", { name: "Main" });
    await user.click(within(nav).getByRole("link", { name: "Profile" }));
    expect(
      await screen.findByRole("heading", { level: 1, name: "Profile" }),
    ).toBeVisible();
    expect(document.title).toBe("Profile · jobapp");
  });

  it("loads the imported profile and shows its completeness in the rail", async () => {
    fakeSession(admin);
    fakeProfile();
    await openProfile("/profile/contact");
    // Search needs a job website ticked in Find jobs (owner, 2026-10-28), which the
    // imported profile does not hold.
    expect(within(rail()).getByText("96% complete")).toBeVisible();
    expect(screen.queryByRole("progressbar")).toBeNull();
    expect(within(rail()).queryByText(/^Missing:/)).toBeNull();
    expect(screen.getByLabelText("Full name")).toHaveValue("Harald Example");
    expect(screen.getByLabelText("Email")).toHaveValue("harald@example.org");
    expect(screen.getByLabelText("Language 3")).toHaveValue("Spanish");
    expect(screen.getByLabelText("Level of language 2")).toHaveValue(
      "Advanced (C1)",
    );
  });

  it("shows an empty profile at 0% with what each use is missing", async () => {
    fakeSession(member);
    fakeProfile(null);
    const { user, location } = await openProfile("/profile/contact");
    expect(within(rail()).getByText("0% complete")).toBeVisible();
    expect(
      within(rail()).getByText(
        /^Missing: full name, headline, email, phone, city, country, at least one language/,
      ),
    ).toBeVisible();
    expect(
      within(rail()).getByText(
        "Missing: at least one employer, at least one school, at least one fact block.",
      ),
    ).toBeVisible();
    expect(screen.getByText("No language yet.")).toBeVisible();
    await user.click(screen.getByRole("tab", { name: "Experience" }));
    expect(location()).toBe("/profile/experience");
    expect(await screen.findByText("No employer yet.")).toBeVisible();
  });

  it("sends the old address of the Search tab to Settings, Search", async () => {
    fakeSession(member);
    fakeProfile(null);
    const { location } = renderApp("/profile?tab=search");
    expect(
      await screen.findByRole("heading", { level: 1, name: "Settings" }),
    ).toBeVisible();
    expect(location()).toBe("/settings/search");
    expect(await screen.findByText("No rule yet.")).toBeVisible();
  });

  it.each([
    ["/profile?tab=identity", "/profile/contact"],
    ["/profile?tab=experience", "/profile/experience"],
    ["/profile?tab=facts", "/profile/about"],
    ["/profile?tab=skills", "/profile/about#about-skills"],
    ["/profile?tab=answers", "/profile/about#about-answers"],
    ["/profile?tab=cv", "/profile"],
    ["/profile/cv", "/profile#your-cv"],
    ["/profile?tab=gone", "/profile"],
    ["/profile#contact", "/profile/contact"],
    ["/profile#experience", "/profile/experience"],
    ["/profile#advanced-matching", "/profile/about#about-skills"],
    ["/profile/matching", "/profile/about#about-skills"],
    ["/profile#about-you", "/profile/about"],
    ["/profile#about-skills", "/profile/about#about-skills"],
    ["/profile#your-cv", "/profile#your-cv"],
  ])("lands the old address %s on its tab", async (old, now) => {
    fakeSession(admin);
    fakeProfile();
    fakeAbout();
    const { location } = await openProfile(old);
    expect(location()).toBe(now);
  });

  it("scrolls to the section the address names once it is drawn", async () => {
    fakeSession(admin);
    fakeProfile();
    const scrolled: string[] = [];
    vi.spyOn(Element.prototype, "scrollIntoView").mockImplementation(function (
      this: Element,
    ) {
      scrolled.push(this.id);
    });
    fakeAbout();
    await openProfile("/profile?tab=answers");
    await waitFor(() => {
      expect(scrolled).toContain("about-answers");
    });
    expect(screen.getByRole("region", { name: "Answers" })).toContainElement(
      document.getElementById("about-answers"),
    );
  });

  it("reports a failed load and retries", async () => {
    fakeSession(admin);
    let calls = 0;
    server.use(
      http.get("*/api/profile", () => {
        calls += 1;
        return HttpResponse.json({ detail: "Boom." }, { status: 500 });
      }),
    );
    const user = userEvent.setup();
    fakeDocuments();
    renderApp("/profile");
    expect(
      await screen.findByText(
        "Your profile could not be loaded. Something went wrong on the server. Try again in a moment.",
      ),
    ).toBeVisible();
    fakeProfile();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(
      await screen.findByRole("list", { name: "Ready for" }),
    ).toBeVisible();
    expect(calls).toBe(1);
  });
});

async function openPage(options: Parameters<typeof fakeAbout>[0] = {}) {
  fakeSession(admin);
  fakeProfile();
  fakeDocuments();
  const about = fakeAbout(options);
  const user = userEvent.setup();
  const app = renderApp("/profile/about");
  await screen.findByRole("heading", { level: 1, name: "Profile" });
  await screen.findByRole("button", { name: "Add a part" });
  return { user, about, ...app };
}

describe("the sections of the page", () => {
  it("shows one part per tab, each at its own address, with Ready for in place of the bar", async () => {
    const { user, location } = await openPage();
    // Tabs, not one long page (owner, 2026-11-07).
    const tabs = within(
      screen.getByRole("tablist", { name: "Parts of your profile" }),
    ).getAllByRole("tab");
    expect(tabs.map((tab) => tab.textContent)).toEqual([
      "CVs",
      "Experience",
      "About you",
      "Contact",
    ]);
    expect(screen.getByRole("tab", { name: "About you" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.queryByRole("progressbar")).toBeNull();
    expect(
      screen
        .getAllByRole("heading", { level: 2 })
        .map((heading) => heading.textContent),
    ).toEqual(["About you"]);
    const ready = screen.getByRole("list", { name: "Ready for" });
    expect(
      within(ready)
        .getAllByRole("listitem")
        .map((line) => line.textContent),
    ).toEqual([
      "Scoring postings: ready",
      "Writing CVs: ready",
      "Filling forms: ready",
    ]);
    expect(
      within(screen.getByRole("region", { name: "You" })).getByText(
        "Harald Example",
      ),
    ).toBeVisible();
    expect(
      screen.getByRole("link", { name: "Fill my profile with an agent" }),
    ).toHaveAttribute("href", "/profile/fill");
    // The jobs and schools, and the identity with the languages, stay forms; the search is in
    // Settings (owner, 2026-11-05).
    await user.click(screen.getByRole("tab", { name: "Experience" }));
    expect(location()).toBe("/profile/experience");
    expect(
      await screen.findByRole("form", { name: "Experience" }),
    ).toBeVisible();
    await user.click(screen.getByRole("tab", { name: "Contact" }));
    expect(await screen.findByLabelText("Full name")).toHaveValue(
      "Harald Example",
    );
    expect(screen.queryByRole("form", { name: "Search" })).toBeNull();
    expect(
      screen.getByRole("link", { name: "Settings, Search" }),
    ).toHaveAttribute("href", "/settings/search");
  });

  it("names the one button that fixes what each use lacks", async () => {
    fakeSession(admin);
    fakeProfile(null);
    fakeDocuments();
    fakeAbout({ text: "## Skills\n\n## Answers\n" });
    renderApp("/profile/about");
    const ready = await screen.findByRole("list", { name: "Ready for" });
    expect(
      within(ready).getByRole("link", { name: "Add your skills" }),
    ).toHaveAttribute("href", "/profile/about#about-skills");
    expect(
      within(ready).getByRole("link", { name: "Add your experience" }),
    ).toHaveAttribute("href", "/profile/experience");
    expect(
      within(ready).getByRole("link", { name: "Add your contact details" }),
    ).toHaveAttribute("href", "/profile/contact");
    // Empty parts say so, and the first of each kind is where Ready for sends the person.
    expect(await screen.findAllByText("Nothing here yet.")).toHaveLength(2);
    expect(document.getElementById("about-skills")).not.toBeNull();
  });

  it("puts History, Export and Import under More actions", async () => {
    const { user } = await openPage();
    await user.click(screen.getByRole("button", { name: "More actions" }));
    const menu = await screen.findByRole("menu");
    expect(
      within(menu).getByRole("menuitem", { name: "Export profile" }),
    ).toBeVisible();
    expect(
      within(menu).getByRole("menuitem", { name: "Import profile" }),
    ).toBeVisible();
    expect(
      within(menu).queryByRole("menuitem", { name: "Classic editor" }),
    ).toBeNull();
    // The History submenu, by the keyboard as a menu is used without a pointer.
    within(menu).getByRole("menuitem", { name: "History" }).focus();
    await user.keyboard("{ArrowRight}");
    await user.click(await screen.findByRole("menuitem", { name: "Skills" }));
    expect(
      await screen.findByRole("dialog", { name: "Skills history" }),
    ).toBeVisible();
  });
});

describe("contact and links", () => {
  it("saves with the loaded revision, toasts, and bases the next save on the new one", async () => {
    fakeSession(admin);
    const state = fakeProfile();
    const { user } = await openProfile("/profile/contact");
    const headline = screen.getByLabelText("Headline");
    await user.clear(headline);
    await user.type(headline, "  Applied AI Engineer ");
    expect(screen.getByText("Unsaved changes")).toBeVisible();
    await user.click(saveButton("identity"));
    expect(await screen.findByText("Identity saved.")).toBeVisible();
    expect(state.saves).toHaveLength(1);
    const first = state.saves[0]!.body;
    expect(first.base_revision).toBe(6);
    expect((first.data as Identity).headline).toBe("Applied AI Engineer");
    expect(first.data).toEqual({
      ...haraldLike().identity,
      headline: "Applied AI Engineer",
    });
    expect(screen.getByLabelText("Headline")).toHaveValue(
      "Applied AI Engineer",
    );
    expect(screen.queryByText("Unsaved changes")).toBeNull();

    await user.clear(screen.getByLabelText("City"));
    await user.click(saveButton("identity"));
    await waitFor(() => {
      expect(state.saves).toHaveLength(2);
    });
    expect(state.saves[1]!.body.base_revision).toBe(7);
    expect((state.saves[1]!.body.data as Identity).city).toBeNull();
    // completeness follows the response
    expect(await within(rail()).findByText("Missing: city.")).toBeVisible();
  });

  it("validates before sending: email, URL, phone, length, duplicate language", async () => {
    fakeSession(admin);
    const state = fakeProfile();
    const { user } = await openProfile("/profile/contact");
    await user.clear(screen.getByLabelText("Email"));
    await user.type(screen.getByLabelText("Email"), "nope");
    await user.clear(screen.getByLabelText("Website"));
    await user.type(screen.getByLabelText("Website"), "example.org");
    await user.clear(screen.getByLabelText("Phone"));
    await user.type(screen.getByLabelText("Phone"), "phone");
    await user.clear(screen.getByLabelText("Full name"));
    await user.click(screen.getByLabelText("Full name"));
    await user.paste("x".repeat(201));
    await user.clear(screen.getByLabelText("Language 2"));
    await user.type(screen.getByLabelText("Language 2"), "FRENCH");
    await user.click(saveButton("identity"));
    expect(
      await screen.findByText("Enter a valid email address."),
    ).toBeVisible();
    expect(
      screen.getByText(
        "Enter a full address starting with http:// or https://.",
      ),
    ).toBeVisible();
    expect(screen.getByText(/Enter a phone number/)).toBeVisible();
    expect(screen.getByText("Use at most 200 characters.")).toBeVisible();
    expect(screen.getByText("This language is already listed.")).toBeVisible();
    expect(screen.getByLabelText("Full name")).toHaveFocus();
    expect(screen.getByLabelText("Email")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    expect(state.saves).toHaveLength(0);
  });

  it("adds, reorders and removes languages", async () => {
    fakeSession(admin);
    const state = fakeProfile();
    const { user } = await openProfile("/profile/contact");
    await user.click(screen.getByRole("button", { name: "Add language" }));
    const added = screen.getByLabelText("Language 4");
    expect(added).toHaveFocus();
    await user.type(added, "Basque");
    await user.click(
      screen.getByRole("button", { name: "Move language 4 up" }),
    );
    expect(screen.getByLabelText("Language 3")).toHaveValue("Basque");
    expect(
      screen.getByRole("button", { name: "Move language 1 up" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Move language 4 down" }),
    ).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Remove language 1" }));
    await user.click(saveButton("identity"));
    await waitFor(() => {
      expect(state.saves).toHaveLength(1);
    });
    expect((state.saves[0]!.body.data as Identity).languages).toEqual([
      { name: "English", level: "Advanced (C1)" },
      { name: "Basque", level: null },
      { name: "Spanish", level: "Intermediate" },
    ]);
  });

  it("puts a 422 from the server under the field it names", async () => {
    fakeSession(admin);
    fakeProfile();
    server.use(
      http.put("*/api/profile/identity", () =>
        HttpResponse.json(
          { detail: "data.email: must be a valid email address" },
          { status: 422 },
        ),
      ),
    );
    const { user } = await openProfile("/profile/contact");
    await user.type(screen.getByLabelText("Headline"), "!");
    await user.click(saveButton("identity"));
    expect(
      await screen.findByText("Must be a valid email address."),
    ).toBeVisible();
    expect(screen.getByLabelText("Email")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    expect(screen.getByLabelText("Email")).toHaveFocus();
  });

  it("shows a 422 it cannot place above the form", async () => {
    fakeSession(admin);
    fakeProfile();
    server.use(
      http.put("*/api/profile/identity", () =>
        HttpResponse.json(
          { detail: "data.surprise: Extra inputs are not permitted" },
          { status: 422 },
        ),
      ),
    );
    const { user } = await openProfile("/profile/contact");
    await user.type(screen.getByLabelText("Headline"), "!");
    await user.click(saveButton("identity"));
    expect(
      await screen.findByText(
        "The server refused this section: data.surprise: Extra inputs are not permitted",
      ),
    ).toBeVisible();
  });

  it("reports a network failure without losing the edits", async () => {
    fakeSession(admin);
    fakeProfile();
    server.use(http.put("*/api/profile/identity", () => HttpResponse.error()));
    const { user } = await openProfile("/profile/contact");
    await user.type(screen.getByLabelText("Headline"), " II");
    await user.click(saveButton("identity"));
    expect(
      await screen.findByText(
        "Identity was not saved. The app cannot reach the server. Check your connection, then try again.",
      ),
    ).toBeVisible();
    expect(screen.getByLabelText("Headline")).toHaveValue("GenAI Engineer II");
  });
});

describe("conflicts", () => {
  async function conflicted() {
    fakeSession(admin);
    const state = fakeProfile();
    const app = await openProfile("/profile/contact");
    // Another tab saves first.
    state.sections.identity = {
      ...haraldLike().identity,
      headline: "Saved elsewhere",
    };
    state.revision = 9;
    await app.user.clear(screen.getByLabelText("Headline"));
    await app.user.type(screen.getByLabelText("Headline"), "Mine");
    await app.user.click(saveButton("identity"));
    const dialog = await screen.findByRole("alertdialog", {
      name: "This section changed elsewhere",
    });
    return { state, dialog, ...app };
  }

  it("Reload discards mine and shows the current version", async () => {
    const { user, dialog, state } = await conflicted();
    await user.click(
      within(dialog).getByRole("button", { name: "Reload (discard mine)" }),
    );
    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).toBeNull();
    });
    expect(screen.getByLabelText("Headline")).toHaveValue("Saved elsewhere");
    expect(screen.queryByText("Unsaved changes")).toBeNull();
    // The next save is based on the reloaded revision.
    await user.type(screen.getByLabelText("Headline"), "!");
    await user.click(saveButton("identity"));
    await waitFor(() => {
      expect(state.saves).toHaveLength(2);
    });
    expect(state.saves[1]!.body.base_revision).toBe(9);
  });

  it("Keep editing keeps mine, and the next save replaces the other version", async () => {
    const { user, dialog, state } = await conflicted();
    await user.click(
      within(dialog).getByRole("button", { name: "Keep editing" }),
    );
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(screen.getByLabelText("Headline")).toHaveValue("Mine");
    await user.click(saveButton("identity"));
    expect(await screen.findByText("Identity saved.")).toBeVisible();
    expect(state.saves.map((s) => s.body.base_revision)).toEqual([6, 9]);
    expect((state.sections.identity as Identity).headline).toBe("Mine");
  });
});

describe("unsaved changes guard", () => {
  it("asks before leaving the page by a link: Keep editing stays, Discard goes", async () => {
    fakeSession(admin);
    fakeProfile();
    const { user, location } = await openProfile("/profile/contact");
    await user.type(screen.getByLabelText("Headline"), " edited");
    await user.click(screen.getByRole("link", { name: "Admin" }));
    const dialog = await screen.findByRole("alertdialog", {
      name: "Discard unsaved changes?",
    });
    expect(dialog).toHaveTextContent(
      "Your changes to Contact and links are not saved.",
    );
    await user.click(
      within(dialog).getByRole("button", { name: "Keep editing" }),
    );
    expect(location()).toBe("/profile/contact");
    expect(screen.getByLabelText("Headline")).toHaveValue(
      "GenAI Engineer edited",
    );

    server.use(
      http.get("*/api/admin/users", () =>
        HttpResponse.json({ items: [], total: 0, limit: 20, offset: 0 }),
      ),
      http.get("*/api/admin/invites", () =>
        HttpResponse.json({ items: [], total: 0, limit: 20, offset: 0 }),
      ),
    );
    await user.click(screen.getByRole("link", { name: "Admin" }));
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", {
        name: "Discard changes",
      }),
    );
    await waitFor(() => {
      expect(location()).toBe("/admin/users");
    });
  });

  it("asks once per navigation, and again for a later change", async () => {
    fakeSession(admin);
    fakeProfile();
    const { user, location, router } = await openProfile("/profile/contact");
    await user.type(screen.getByLabelText("Headline"), " edited");
    await user.click(
      screen.getByRole("link", { name: "Fill my profile with an agent" }),
    );
    await user.click(
      within(
        await screen.findByRole("alertdialog", {
          name: "Discard unsaved changes?",
        }),
      ).getByRole("button", { name: "Discard changes" }),
    );
    await waitFor(() => {
      expect(location()).toBe("/profile/fill");
    });
    expect(screen.queryByRole("alertdialog")).toBeNull();

    // Back on the profile, the saved value shows and a new change is guarded again.
    await router.navigate("/profile/contact");
    const headline = await screen.findByLabelText("Headline");
    expect(headline).toHaveValue("GenAI Engineer");
    await user.type(headline, "!");
    await user.click(screen.getByRole("link", { name: "Admin" }));
    expect(
      await screen.findByRole("alertdialog", {
        name: "Discard unsaved changes?",
      }),
    ).toBeVisible();
    expect(location()).toBe("/profile/contact");
  });

  it("asks before another tab replaces a form with unsaved changes", async () => {
    fakeSession(admin);
    fakeProfile();
    const { user, location } = await openProfile("/profile/contact");
    await user.type(screen.getByLabelText("Headline"), "!");
    await user.click(screen.getByRole("tab", { name: "Experience" }));
    const dialog = await screen.findByRole("alertdialog", {
      name: "Discard unsaved changes?",
    });
    expect(dialog).toHaveTextContent(
      "Your changes to Contact and links are not saved.",
    );
    await user.click(
      within(dialog).getByRole("button", { name: "Keep editing" }),
    );
    expect(location()).toBe("/profile/contact");
    expect(screen.getByLabelText("Headline")).toHaveValue("GenAI Engineer!");
  });

  it("follows a link without asking when nothing changed", async () => {
    fakeSession(admin);
    fakeProfile();
    const { user, location } = await openProfile();
    await user.click(
      screen.getByRole("link", { name: "Fill my profile with an agent" }),
    );
    expect(screen.queryByRole("alertdialog")).toBeNull();
    await waitFor(() => {
      expect(location()).toBe("/profile/fill");
    });
  });

  it("asks the browser to confirm closing the page only while dirty", async () => {
    fakeSession(admin);
    fakeProfile();
    const { user } = await openProfile("/profile/contact");
    const clean = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(clean);
    expect(clean.defaultPrevented).toBe(false);
    await user.type(screen.getByLabelText("Headline"), "!");
    const dirty = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(dirty);
    expect(dirty.defaultPrevented).toBe(true);
  });
});

describe("several forms, one profile", () => {
  it("bases a save on the revision another form's save made, with no conflict", async () => {
    fakeSession(admin);
    const state = fakeProfile();
    const { user } = await openProfile("/profile/contact");
    await user.type(screen.getByLabelText("Headline"), "!");
    await user.click(saveButton("identity"));
    expect(await screen.findByText("Identity saved.")).toBeVisible();
    await user.click(screen.getByRole("tab", { name: "Experience" }));
    await user.type(
      await screen.findByLabelText("Job title at employer 1"),
      "!",
    );
    await user.click(saveButton("experience"));
    expect(await screen.findByText("Experience saved.")).toBeVisible();
    expect(state.saves.map((one) => one.body.base_revision)).toEqual([6, 7]);
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it("saves the skills fields and keeps the categories stored as regular expressions", async () => {
    fakeSession(admin);
    const state = fakeProfile();
    const { user } = await openProfile("/profile/about");
    const skills = await screen.findByRole("form", { name: "Skills" });
    const years = within(skills).getByLabelText("Years of experience");
    await user.clear(years);
    await user.type(years, "5");
    await user.click(
      within(skills).getByRole("button", { name: "Save skills" }),
    );
    await waitFor(() => {
      expect(state.saves).toHaveLength(1);
    });
    // Advanced matching is gone (owner, 2026-11-08): the expressions are kept as stored.
    const sent = state.saves[0]!.body.data as Skills;
    expect(sent.years_experience).toBe(5);
    const stored = haraldLike().skills.have.filter((one) => one.advanced_regex);
    expect(stored.length).toBeGreaterThan(0);
    expect(sent.have.filter((one) => one.advanced_regex)).toEqual(stored);
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it("still asks when the same section was saved elsewhere meanwhile", async () => {
    fakeSession(admin);
    const state = fakeProfile();
    const { user } = await openProfile("/profile/contact");
    await user.type(screen.getByLabelText("Headline"), "!");
    await user.click(saveButton("identity"));
    expect(await screen.findByText("Identity saved.")).toBeVisible();
    // Another browser tab changes the experience: this page still holds the old one.
    state.sections.experience = { employers: [], education: [] };
    state.revision += 1;
    await user.click(screen.getByRole("tab", { name: "Experience" }));
    await user.type(
      await screen.findByLabelText("Job title at employer 1"),
      "!",
    );
    await user.click(saveButton("experience"));
    expect(
      await screen.findByRole("alertdialog", {
        name: "This section changed elsewhere",
      }),
    ).toBeVisible();
  });
});

describe("experience and education", () => {
  it("adds an employer with a key made from the organisation, reorders and saves", async () => {
    fakeSession(admin);
    const state = fakeProfile();
    const { user } = await openProfile("/profile/experience");
    expect(screen.getByLabelText("Organisation of employer 1")).toHaveValue(
      "Lumetra Group",
    );
    expect(screen.getByLabelText("Present at employer 1")).toBeChecked();
    expect(screen.getByLabelText("End of employer 1")).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Add employer" }));
    const org = screen.getByLabelText("Organisation of employer 4");
    expect(org).toHaveFocus();
    await user.type(org, "Zoë & Co");
    expect(screen.getByLabelText("Key of employer 4")).toHaveValue("zoe-co");
    await user.type(
      screen.getByLabelText("Job title at employer 4"),
      "Engineer",
    );
    await user.type(screen.getByLabelText("Start of employer 4"), "2021-03");
    await user.type(screen.getByLabelText("End of employer 4"), "2022-02");
    await user.selectOptions(
      screen.getByLabelText("Contract at employer 4"),
      "internship",
    );
    for (let i = 0; i < 3; i += 1) {
      const n = 4 - i;
      await user.click(
        screen.getByRole("button", { name: `Move employer ${n} up` }),
      );
    }
    expect(screen.getByLabelText("Organisation of employer 1")).toHaveValue(
      "Zoë & Co",
    );
    await user.click(
      screen.getByRole("button", { name: "Move employer 1 down" }),
    );
    await user.click(saveButton("experience"));
    expect(await screen.findByText("Experience saved.")).toBeVisible();
    const sent = state.saves[0]!.body.data as Experience;
    expect(sent.employers.map((e) => e.key)).toEqual([
      "lumetra",
      "zoe-co",
      "noxtech",
      "olmea",
    ]);
    expect(sent.employers[1]).toEqual<Employer>({
      key: "zoe-co",
      organisation: "Zoë & Co",
      title: "Engineer",
      start: "2021-03",
      end: "2022-02",
      location: null,
      contract: "internship",
    });
    expect(sent.education).toEqual(haraldLike().experience.education);
  });

  it("stops following the organisation once the key is edited by hand", async () => {
    fakeSession(admin);
    fakeProfile();
    const { user } = await openProfile("/profile/experience");
    await user.click(screen.getByRole("button", { name: "Add employer" }));
    await user.type(
      screen.getByLabelText("Organisation of employer 4"),
      "Acme",
    );
    const key = screen.getByLabelText("Key of employer 4");
    await user.clear(key);
    await user.type(key, "custom");
    await user.type(
      screen.getByLabelText("Organisation of employer 4"),
      " Corp",
    );
    expect(key).toHaveValue("custom");
    // An imported employer's key never follows its organisation.
    await user.type(
      screen.getByLabelText("Organisation of employer 1"),
      " SAS",
    );
    expect(screen.getByLabelText("Key of employer 1")).toHaveValue("lumetra");
  });

  it("validates dates, end before start, present, duplicate and malformed keys", async () => {
    fakeSession(admin);
    const state = fakeProfile();
    const { user } = await openProfile("/profile/experience");
    await user.clear(screen.getByLabelText("Start of employer 3"));
    await user.type(screen.getByLabelText("Start of employer 3"), "2023-13");
    await user.clear(screen.getByLabelText("End of employer 3"));
    await user.click(screen.getByLabelText("Present at employer 2"));
    await user.clear(screen.getByLabelText("Key of employer 2"));
    await user.type(screen.getByLabelText("Key of employer 2"), "lumetra");
    await user.clear(screen.getByLabelText("Start of school 1"));
    await user.type(screen.getByLabelText("Start of school 1"), "2026-01");
    await user.click(screen.getByRole("button", { name: "Add employer" }));
    await user.type(screen.getByLabelText("Organisation of employer 4"), "Bad");
    await user.clear(screen.getByLabelText("Key of employer 4"));
    await user.type(screen.getByLabelText("Key of employer 4"), "Bad Key");
    await user.click(saveButton("experience"));
    expect(
      await screen.findByText(
        "Write the date as YYYY or YYYY-MM, between 1900 and 2100.",
      ),
    ).toBeVisible();
    // employer 2 untick present, employer 3 end cleared, employer 4 new
    expect(screen.getAllByText("Enter the end, or tick Present.")).toHaveLength(
      3,
    );
    expect(
      screen.getByText("Another employer already uses this key."),
    ).toBeVisible();
    expect(screen.getByText("The end is before the start.")).toBeVisible();
    expect(
      screen.getByText(
        "Use lowercase letters and digits, words joined by single hyphens.",
      ),
    ).toBeVisible();
    expect(screen.getByText("Enter the job title.")).toBeVisible();
    expect(state.saves).toHaveLength(0);
  });

  it("ticking present clears and disables the end and sends null", async () => {
    fakeSession(admin);
    const state = fakeProfile();
    const { user } = await openProfile("/profile/experience");
    const end = screen.getByLabelText("End of employer 3");
    expect(end).toHaveValue("2024-06");
    await user.click(screen.getByLabelText("Present at employer 3"));
    expect(end).toHaveValue("");
    expect(end).toBeDisabled();
    await user.click(saveButton("experience"));
    await waitFor(() => {
      expect(state.saves).toHaveLength(1);
    });
    expect(
      (state.saves[0]!.body.data as Experience).employers[2]!.end,
    ).toBeNull();
  });

  it("takes a year alone, says how it reads it, and saves it as typed", async () => {
    fakeSession(member);
    const state = fakeProfile(null);
    const { user } = await openProfile("/profile/experience");
    await user.click(screen.getByRole("button", { name: "Add employer" }));
    await user.type(
      screen.getByLabelText("Organisation of employer 1"),
      "Northwind Labs",
    );
    await user.type(
      screen.getByLabelText("Job title at employer 1"),
      "Engineer",
    );
    const start = screen.getByLabelText("Start of employer 1");
    const end = screen.getByLabelText("End of employer 1");
    expect(start).toHaveAttribute("placeholder", "YYYY or YYYY-MM");
    // The hint is the field's description, so a screen reader says it with the label.
    expect(start).toHaveAccessibleDescription(
      "A year alone or a month: 2021 or 2021-03.",
    );
    await user.type(start, "2021");
    expect(start).toHaveValue("2021");
    expect(start).toHaveAccessibleDescription("Read as the year 2021.");
    await user.type(end, "2023");
    expect(end).toHaveAccessibleDescription("Read as the year 2023.");

    await user.click(screen.getByRole("button", { name: "Add school" }));
    await user.type(screen.getByLabelText("School 1"), "École Exemple");
    await user.type(screen.getByLabelText("Degree at school 1"), "MSc");
    await user.type(screen.getByLabelText("Start of school 1"), "2019");

    await user.click(saveButton("experience"));
    expect(await screen.findByText("Experience saved.")).toBeVisible();
    const sent = state.saves[0]!.body.data as Experience;
    expect(sent.employers[0]).toMatchObject({ start: "2021", end: "2023" });
    expect(sent.education[0]).toMatchObject({ start: "2019", end: null });
    // The saved form shows the same four characters, not a month made up for them.
    expect(screen.getByLabelText("Start of employer 1")).toHaveValue("2021");
    expect(screen.getByLabelText("End of employer 1")).toHaveValue("2023");
    expect(screen.getByLabelText("Start of school 1")).toHaveValue("2019");
    expect(screen.getByLabelText("End of school 1")).toHaveValue("");
  });

  it("shows stored years as stored, and a year becomes a month only when one is typed", async () => {
    fakeSession(admin);
    const experience = haraldLike().experience;
    experience.employers[2] = {
      ...experience.employers[2]!,
      start: "2021",
      end: "2023",
    };
    const state = fakeProfile({ ...haraldLike(), experience });
    const { user } = await openProfile("/profile/experience");
    const start = screen.getByLabelText("Start of employer 3");
    expect(start).toHaveValue("2021");
    expect(start).toHaveAccessibleDescription("Read as the year 2021.");
    expect(screen.getByLabelText("End of employer 3")).toHaveValue("2023");
    expect(screen.getByLabelText("Start of employer 1")).toHaveValue("2025-03");
    expect(
      screen.getByLabelText("Start of employer 1"),
    ).toHaveAccessibleDescription("Read as March 2025.");

    // Six digits typed on a number pad, which has no hyphen key.
    await user.clear(start);
    await user.type(start, "202103");
    expect(start).toHaveValue("2021-03");
    expect(start).toHaveAccessibleDescription("Read as March 2021.");
    await user.click(saveButton("experience"));
    await waitFor(() => {
      expect(state.saves).toHaveLength(1);
    });
    const sent = state.saves[0]!.body.data as Experience;
    expect(sent.employers[2]).toMatchObject({ start: "2021-03", end: "2023" });
    // The other dates went back exactly as they came.
    expect(sent.employers.slice(0, 2)).toEqual(
      haraldLike().experience.employers.slice(0, 2),
    );
    expect(sent.education).toEqual(haraldLike().experience.education);
  });

  it("refuses only an end that is certainly before the start", async () => {
    fakeSession(admin);
    const state = fakeProfile();
    const { user } = await openProfile("/profile/experience");
    const start = screen.getByLabelText("Start of employer 3");
    const end = screen.getByLabelText("End of employer 3");
    await user.clear(start);
    await user.type(start, "2023");
    await user.clear(end);
    await user.type(end, "2022-12");
    await user.click(saveButton("experience"));
    expect(
      await screen.findByText("The end is before the start."),
    ).toBeVisible();
    expect(end).toHaveAttribute("aria-invalid", "true");
    expect(end).toHaveAccessibleDescription(
      "Read as December 2022. The end is before the start.",
    );
    expect(state.saves).toHaveLength(0);

    // 2023-05 to 2023: the end may be December, so it is in order.
    await user.clear(start);
    await user.type(start, "2023-05");
    await user.clear(end);
    await user.type(end, "2023");
    await user.click(saveButton("experience"));
    await waitFor(() => {
      expect(state.saves).toHaveLength(1);
    });
    expect(
      (state.saves[0]!.body.data as Experience).employers[2],
    ).toMatchObject({ start: "2023-05", end: "2023" });
  });

  it("with Present ticked a year alone start is saved and the end has no hint", async () => {
    fakeSession(member);
    const state = fakeProfile(null);
    const { user } = await openProfile("/profile/experience");
    await user.click(screen.getByRole("button", { name: "Add employer" }));
    await user.type(
      screen.getByLabelText("Organisation of employer 1"),
      "Northwind Labs",
    );
    await user.type(
      screen.getByLabelText("Job title at employer 1"),
      "Engineer",
    );
    await user.type(screen.getByLabelText("Start of employer 1"), "2024");
    await user.type(screen.getByLabelText("End of employer 1"), "2020");
    await user.click(screen.getByLabelText("Present at employer 1"));
    const end = screen.getByLabelText("End of employer 1");
    expect(end).toBeDisabled();
    expect(end).toHaveValue("");
    expect(end).not.toHaveAccessibleDescription();
    await user.click(saveButton("experience"));
    await waitFor(() => {
      expect(state.saves).toHaveLength(1);
    });
    expect(
      (state.saves[0]!.body.data as Experience).employers[0],
    ).toMatchObject({ start: "2024", end: null });
  });

  it("maps the server's end before start and duplicate key errors to the fields", async () => {
    fakeSession(admin);
    fakeProfile();
    let answer = "data.education.1: end must not be before start";
    server.use(
      http.put("*/api/profile/experience", () =>
        HttpResponse.json({ detail: answer }, { status: 422 }),
      ),
    );
    const { user } = await openProfile("/profile/experience");
    await user.type(screen.getByLabelText("Location of school 2"), "Kervan");
    await user.click(saveButton("experience"));
    expect(
      await screen.findByText("End must not be before start."),
    ).toBeVisible();
    expect(screen.getByLabelText("End of school 2")).toHaveAttribute(
      "aria-invalid",
      "true",
    );

    answer = "data.employers: duplicate employer key: each must be unique";
    await user.click(saveButton("experience"));
    // No duplicate in the form: the first item's key carries the message.
    expect(
      await screen.findByText("Duplicate employer key: each must be unique."),
    ).toBeVisible();
    expect(screen.getByLabelText("Key of employer 1")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
  });

  it("adds and removes schools", async () => {
    fakeSession(admin);
    const state = fakeProfile();
    const { user } = await openProfile("/profile/experience");
    await user.click(screen.getByRole("button", { name: "Remove school 1" }));
    await user.click(screen.getByRole("button", { name: "Add school" }));
    await user.type(screen.getByLabelText("School 2"), "Lycée Exemple");
    await user.type(
      screen.getByLabelText("Degree at school 2"),
      "Baccalauréat",
    );
    await user.type(
      screen.getByLabelText("Details of school 2"),
      "Line one{Enter}Line two",
    );
    await user.click(saveButton("experience"));
    await waitFor(() => {
      expect(state.saves).toHaveLength(1);
    });
    expect((state.saves[0]!.body.data as Experience).education).toEqual([
      haraldLike().experience.education[1],
      {
        school: "Lycée Exemple",
        degree: "Baccalauréat",
        start: null,
        end: null,
        location: null,
        details: "Line one\nLine two",
      },
    ]);
  });
});
