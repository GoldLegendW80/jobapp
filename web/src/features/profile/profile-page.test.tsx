import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { toast } from "sonner";
import { afterEach, describe, expect, it } from "vitest";

import { admin, fakeSession, member } from "@/test/auth";
import { fakeProfile, haraldLike, XSS_BODY } from "@/test/profile";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import type { Employer, Experience, Identity } from "./api";

// Sonner keeps its toasts in a module store: clear them so one test never sees another's.
afterEach(() => {
  toast.dismiss();
});

async function openProfile(path = "/profile") {
  const user = userEvent.setup();
  const app = renderApp(path);
  await screen.findByRole("heading", { level: 1, name: "Profile" });
  return { user, ...app };
}

const tab = (name: RegExp) => screen.getByRole("tab", { name });
const saveButton = (section: string) =>
  screen.getByRole("button", { name: `Save ${section}` });

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

  it("loads the imported profile and shows completeness overall and per tab", async () => {
    fakeSession(admin);
    fakeProfile();
    await openProfile();
    const bar = screen.getByRole("progressbar", {
      name: "Profile completeness",
    });
    expect(bar).toHaveAttribute("aria-valuenow", "100");
    expect(screen.getByText("100%")).toBeVisible();
    expect(screen.getAllByRole("tab")).toHaveLength(6);
    for (const name of [
      "Identity",
      "Experience",
      "Facts",
      "Skills",
      "Search",
      "Answers",
    ]) {
      expect(tab(new RegExp(`^${name}, 100% complete$`))).toBeInTheDocument();
    }
    expect(tab(/Identity/)).toHaveAttribute("aria-selected", "true");
    expect(screen.getByLabelText("Full name")).toHaveValue("Harald Example");
    expect(screen.getByLabelText("Email")).toHaveValue("harald@example.org");
    expect(screen.getByLabelText("Language 3")).toHaveValue("Spanish");
    expect(screen.getByLabelText("Level of language 2")).toHaveValue(
      "Advanced (C1)",
    );
    expect(screen.queryByText(/To complete this section/)).toBeNull();
  });

  it("shows an empty profile at 0% with what is missing", async () => {
    fakeSession(member);
    fakeProfile(null);
    const { user } = await openProfile();
    expect(screen.getByRole("progressbar")).toHaveAttribute(
      "aria-valuenow",
      "0",
    );
    expect(tab(/^Identity, 0% complete$/)).toBeInTheDocument();
    expect(
      screen.getByText(/To complete this section, add: full name, headline/),
    ).toBeVisible();
    expect(screen.getByText("No language yet.")).toBeVisible();
    await user.click(tab(/Experience/));
    expect(await screen.findByText("No employer yet.")).toBeVisible();
    await user.click(tab(/Facts/));
    expect(await screen.findByText("No fact block yet.")).toBeVisible();
    await user.click(tab(/Search/));
    expect(await screen.findByText("No board yet.")).toBeVisible();
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
    renderApp("/profile");
    expect(
      await screen.findByText("Your profile could not be loaded"),
    ).toBeVisible();
    expect(screen.getByText("The API answered 500: Boom..")).toBeVisible();
    fakeProfile();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("progressbar")).toBeVisible();
    expect(calls).toBe(1);
  });

  it("opens the tab named in the address", async () => {
    fakeSession(admin);
    fakeProfile();
    await openProfile("/profile?tab=answers");
    expect(tab(/Answers/)).toHaveAttribute("aria-selected", "true");
    expect(screen.getByLabelText("Notice period")).toHaveValue(
      "1 to 3 months.",
    );
  });
});

describe("read only tabs", () => {
  it("lists fact blocks with bodies rendered safely as Markdown", async () => {
    fakeSession(admin);
    fakeProfile();
    const { user } = await openProfile();
    await user.click(tab(/Facts/));
    expect(await screen.findByText("Coming next:")).toBeVisible();
    const summary = screen.getByText("POSITIONING");
    await user.click(summary);
    const block = summary.closest("details")!;
    expect(block).toHaveAttribute("open");
    expect(block.querySelector("script")).toBeNull();
    expect(block.querySelector("img")).toBeNull();
    expect(block.textContent).toContain("<script>");
    expect(
      within(block).getByRole("link", { name: "safe link" }),
    ).toHaveAttribute("href", "https://example.org");
    expect(within(block).queryByRole("link", { name: "bad link" })).toBeNull();
    expect((window as { __xss?: string }).__xss).toBeUndefined();
    expect(XSS_BODY).toContain("onerror");
  });

  it("shows skills categories with their terms", async () => {
    fakeSession(admin);
    fakeProfile();
    const { user } = await openProfile();
    await user.click(tab(/Skills/));
    expect(await screen.findByText("3.5 years of experience.")).toBeVisible();
    const agents = screen.getByRole("list", { name: "Terms of agents" });
    expect(
      within(agents)
        .getAllByRole("listitem")
        .map((li) => li.textContent),
    ).toEqual(["agent", "langgraph"]);
    expect(
      screen.getByRole("list", { name: "Terms of llm" }),
    ).toHaveTextContent("\\bllm(?:s)?\\b");
    expect(screen.getByText("(penalty 25)")).toBeVisible();
    expect(screen.getByText("Coming next:")).toBeVisible();
  });

  it("shows search terms, preferences and boards", async () => {
    fakeSession(admin);
    fakeProfile();
    const { user } = await openProfile();
    await user.click(tab(/Search/));
    const boards = await screen.findByRole("list", { name: "Boards" });
    expect(within(boards).getAllByRole("listitem")).toHaveLength(2);
    expect(boards).toHaveTextContent("Mistral AI");
    expect(boards).toHaveTextContent("ashby, mistral.ai");
    expect(within(boards).getByText("Off")).toBeVisible();
    expect(screen.getByRole("list", { name: "Locations" })).toHaveTextContent(
      "Paris",
    );
    expect(screen.getByText("CDI, Freelance")).toBeVisible();
    expect(screen.getByText("No cybersecurity roles.")).toBeVisible();
  });
});

describe("identity tab", () => {
  it("saves with the loaded revision, toasts, and bases the next save on the new one", async () => {
    fakeSession(admin);
    const state = fakeProfile();
    const { user } = await openProfile();
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
    expect(
      await screen.findByRole("tab", { name: /^Identity, 86% complete$/ }),
    ).toBeVisible();
    expect(
      screen.getByText(/To complete this section, add: city\./),
    ).toBeVisible();
  });

  it("validates before sending: email, URL, phone, length, duplicate language", async () => {
    fakeSession(admin);
    const state = fakeProfile();
    const { user } = await openProfile();
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
    const { user } = await openProfile();
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
    const { user } = await openProfile();
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
    const { user } = await openProfile();
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
    const { user } = await openProfile();
    await user.type(screen.getByLabelText("Headline"), " II");
    await user.click(saveButton("identity"));
    expect(
      await screen.findByText(
        "Identity was not saved. The API could not be reached.",
      ),
    ).toBeVisible();
    expect(screen.getByLabelText("Headline")).toHaveValue("GenAI Engineer II");
  });
});

describe("conflicts", () => {
  async function conflicted() {
    fakeSession(admin);
    const state = fakeProfile();
    const app = await openProfile();
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
  it("asks before switching tab: Keep editing stays, Discard switches", async () => {
    fakeSession(admin);
    fakeProfile();
    const { user, location } = await openProfile();
    await user.type(screen.getByLabelText("Headline"), " edited");
    await user.click(tab(/Experience/));
    const dialog = await screen.findByRole("alertdialog", {
      name: "Discard unsaved changes?",
    });
    expect(dialog).toHaveTextContent("Your changes to Identity are not saved.");
    await user.click(
      within(dialog).getByRole("button", { name: "Keep editing" }),
    );
    expect(tab(/Identity/)).toHaveAttribute("aria-selected", "true");
    expect(screen.getByLabelText("Headline")).toHaveValue(
      "GenAI Engineer edited",
    );

    await user.click(tab(/Answers/));
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", {
        name: "Discard changes",
      }),
    );
    expect(tab(/Answers/)).toHaveAttribute("aria-selected", "true");
    expect(location()).toBe("/profile?tab=answers");
    // Back on Identity, the saved value is shown again and no guard is left behind.
    await user.click(tab(/Identity/));
    expect(screen.getByLabelText("Headline")).toHaveValue("GenAI Engineer");
  });

  it("switches tab without asking when nothing changed", async () => {
    fakeSession(admin);
    fakeProfile();
    const { user } = await openProfile();
    await user.click(tab(/Experience/));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(tab(/Experience/)).toHaveAttribute("aria-selected", "true");
  });

  it("asks before leaving the page by a link", async () => {
    fakeSession(admin);
    fakeProfile();
    const { user, location } = await openProfile();
    await user.type(screen.getByLabelText("Headline"), " edited");
    await user.click(screen.getByRole("link", { name: "Admin" }));
    const dialog = await screen.findByRole("alertdialog", {
      name: "Discard unsaved changes?",
    });
    await user.click(
      within(dialog).getByRole("button", { name: "Keep editing" }),
    );
    expect(location()).toBe("/profile");
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

  it("asks the browser to confirm closing the page only while dirty", async () => {
    fakeSession(admin);
    fakeProfile();
    const { user } = await openProfile();
    const clean = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(clean);
    expect(clean.defaultPrevented).toBe(false);
    await user.type(screen.getByLabelText("Headline"), "!");
    const dirty = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(dirty);
    expect(dirty.defaultPrevented).toBe(true);
  });
});

describe("experience tab", () => {
  it("adds an employer with a key made from the organisation, reorders and saves", async () => {
    fakeSession(admin);
    const state = fakeProfile();
    const { user } = await openProfile("/profile?tab=experience");
    expect(screen.getByLabelText("Organisation of employer 1")).toHaveValue(
      "Betclic Group",
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
      "betclic",
      "zoe-co",
      "devtech",
      "elsan",
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
    const { user } = await openProfile("/profile?tab=experience");
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
    expect(screen.getByLabelText("Key of employer 1")).toHaveValue("betclic");
  });

  it("validates months, end before start, present, duplicate and malformed keys", async () => {
    fakeSession(admin);
    const state = fakeProfile();
    const { user } = await openProfile("/profile?tab=experience");
    await user.clear(screen.getByLabelText("Start of employer 3"));
    await user.type(screen.getByLabelText("Start of employer 3"), "2023-13");
    await user.clear(screen.getByLabelText("End of employer 3"));
    await user.click(screen.getByLabelText("Present at employer 2"));
    await user.clear(screen.getByLabelText("Key of employer 2"));
    await user.type(screen.getByLabelText("Key of employer 2"), "betclic");
    await user.clear(screen.getByLabelText("Start of school 1"));
    await user.type(screen.getByLabelText("Start of school 1"), "2026-01");
    await user.click(screen.getByRole("button", { name: "Add employer" }));
    await user.type(screen.getByLabelText("Organisation of employer 4"), "Bad");
    await user.clear(screen.getByLabelText("Key of employer 4"));
    await user.type(screen.getByLabelText("Key of employer 4"), "Bad Key");
    await user.click(saveButton("experience"));
    expect(
      await screen.findByText(
        "Write the month as YYYY-MM, between 1900 and 2100.",
      ),
    ).toBeVisible();
    // employer 2 untick present, employer 3 end cleared, employer 4 new
    expect(
      screen.getAllByText("Enter the end month, or tick Present."),
    ).toHaveLength(3);
    expect(
      screen.getByText("Another employer already uses this key."),
    ).toBeVisible();
    expect(
      screen.getByText("The end month is before the start month."),
    ).toBeVisible();
    expect(
      screen.getByText(
        "Use lowercase letters and digits, words joined by single hyphens.",
      ),
    ).toBeVisible();
    expect(screen.getByText("Enter the job title.")).toBeVisible();
    expect(state.saves).toHaveLength(0);
  });

  it("ticking present clears and disables the end month and sends null", async () => {
    fakeSession(admin);
    const state = fakeProfile();
    const { user } = await openProfile("/profile?tab=experience");
    const end = screen.getByLabelText("End of employer 3");
    expect(end).toHaveValue("2025-08");
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

  it("maps the server's end before start and duplicate key errors to the fields", async () => {
    fakeSession(admin);
    fakeProfile();
    let answer = "data.education.1: end must not be before start";
    server.use(
      http.put("*/api/profile/experience", () =>
        HttpResponse.json({ detail: answer }, { status: 422 }),
      ),
    );
    const { user } = await openProfile("/profile?tab=experience");
    await user.type(screen.getByLabelText("Location of school 2"), "Reims");
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
    const { user } = await openProfile("/profile?tab=experience");
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

describe("answers tab", () => {
  it("edits every kind of field and saves", async () => {
    fakeSession(admin);
    const state = fakeProfile();
    const { user } = await openProfile("/profile?tab=answers");
    await user.selectOptions(
      screen.getByLabelText("Needs visa sponsorship in country 2"),
      "",
    );
    await user.click(screen.getByRole("button", { name: "Add country" }));
    await user.type(
      screen.getByLabelText("Country or region 3"),
      "Switzerland",
    );
    await user.type(
      screen.getByLabelText("Answer for country 3"),
      "Permit needed.",
    );
    await user.selectOptions(
      screen.getByLabelText("Needs visa sponsorship in country 3"),
      "yes",
    );
    await user.clear(screen.getByLabelText("Motivation"));
    await user.type(
      screen.getByLabelText("Motivation"),
      "First line{Enter}Second line",
    );
    await user.clear(screen.getByLabelText("Travel"));
    await user.click(screen.getByRole("button", { name: "Add question" }));
    await user.type(
      screen.getByLabelText("Question 2 to leave blank"),
      "Gender",
    );
    await user.click(saveButton("answers"));
    expect(await screen.findByText("Answers saved.")).toBeVisible();
    const sent = state.saves[0]!.body.data as ReturnType<
      typeof haraldLike
    >["answers"];
    expect(sent.work_authorization).toEqual([
      haraldLike().answers.work_authorization[0],
      {
        country: "United Kingdom",
        answer: "Sponsorship would be needed.",
        needs_sponsorship: null,
      },
      {
        country: "Switzerland",
        answer: "Permit needed.",
        needs_sponsorship: true,
      },
    ]);
    expect(sent.motivation).toBe("First line\nSecond line");
    expect(sent.travel).toBeNull();
    expect(sent.leave_blank).toEqual([
      "Voluntary self identification",
      "Gender",
    ]);
  });

  it("refuses a duplicate country and an over long motivation", async () => {
    fakeSession(admin);
    const state = fakeProfile();
    const { user } = await openProfile("/profile?tab=answers");
    await user.clear(screen.getByLabelText("Country or region 2"));
    await user.type(screen.getByLabelText("Country or region 2"), "france");
    await user.clear(screen.getByLabelText("Motivation"));
    await user.click(screen.getByLabelText("Motivation"));
    await user.paste("m".repeat(10_001));
    await user.click(saveButton("answers"));
    expect(
      await screen.findByText("This country is already listed."),
    ).toBeVisible();
    expect(screen.getByText("Use at most 10,000 characters.")).toBeVisible();
    expect(state.saves).toHaveLength(0);
  });

  it("maps a server error on a leave blank entry to its field", async () => {
    fakeSession(admin);
    fakeProfile();
    server.use(
      http.put("*/api/profile/answers", () =>
        HttpResponse.json(
          { detail: "data.leave_blank.0: control characters are not allowed" },
          { status: 422 },
        ),
      ),
    );
    const { user } = await openProfile("/profile?tab=answers");
    await user.type(screen.getByLabelText("Travel"), "!");
    await user.click(saveButton("answers"));
    expect(
      await screen.findByText("Control characters are not allowed."),
    ).toBeVisible();
    expect(screen.getByLabelText("Question 1 to leave blank")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
  });
});
