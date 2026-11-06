import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { admin, fakeSession } from "@/test/auth";
import { ABOUT_TEXT, fakeAbout } from "@/test/about";
import { fakeDocuments } from "@/test/documents";
import { fakeProfile } from "@/test/profile";
import { renderApp } from "@/test/render";

import { READING_POLL_MS } from "./about-api";

afterEach(() => {
  vi.useRealTimers();
});

async function openPage(options: Parameters<typeof fakeAbout>[0] = {}) {
  fakeSession(admin);
  fakeProfile();
  fakeDocuments();
  const about = fakeAbout(options);
  const user = userEvent.setup();
  const app = renderApp("/profile");
  await screen.findByRole("heading", { level: 1, name: "Profile" });
  await screen.findByRole("navigation", { name: "Parts of About you" });
  return { user, about, ...app };
}

const part = (name: string) => screen.getByRole("region", { name });

describe("the one page profile", () => {
  it("shows its parts top to bottom, no tabs, with Ready for in place of the bar", async () => {
    await openPage();
    expect(screen.queryByRole("tab")).toBeNull();
    expect(screen.queryByRole("progressbar")).toBeNull();
    const headings = screen
      .getAllByRole("heading", { level: 2 })
      .map((heading) => heading.textContent);
    expect(headings).toEqual([
      "Your CVs",
      "Experience and education",
      "About you",
      "Contact and links",
    ]);
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
      screen.getByRole("link", { name: "Add other documents" }),
    ).toHaveAttribute("href", "/profile/fill");
    // The jobs and schools, and the identity with the languages, stay forms; the search is in
    // Settings (owner, 2026-11-05).
    expect(screen.getByRole("form", { name: "Experience" })).toBeVisible();
    expect(screen.getByLabelText("Full name")).toHaveValue("Harald Example");
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
    renderApp("/profile");
    const ready = await screen.findByRole("list", { name: "Ready for" });
    expect(
      within(ready).getByRole("link", { name: "Add your skills" }),
    ).toHaveAttribute("href", "#about-skills");
    expect(
      within(ready).getByRole("link", { name: "Add your experience" }),
    ).toHaveAttribute("href", "#experience");
    expect(
      within(ready).getByRole("link", { name: "Add your contact details" }),
    ).toHaveAttribute("href", "#contact");
    // Empty parts say so, and the first of each kind is where Ready for sends the person.
    expect(await screen.findAllByText("Nothing here yet.")).toHaveLength(2);
    expect(document.getElementById("about-skills")).not.toBeNull();
    expect(document.getElementById("experience")).not.toBeNull();
  });

  it("puts History, Export, Import and the classic editor under More", async () => {
    const { user, location } = await openPage();
    await user.click(screen.getByRole("button", { name: "More" }));
    const menu = await screen.findByRole("menu");
    expect(
      within(menu).getByRole("menuitem", { name: "Export profile" }),
    ).toBeVisible();
    expect(
      within(menu).getByRole("menuitem", { name: "Import profile" }),
    ).toBeVisible();
    // The History submenu, by the keyboard as a menu is used without a pointer.
    within(menu).getByRole("menuitem", { name: "History" }).focus();
    await user.keyboard("{ArrowRight}");
    await user.click(await screen.findByRole("menuitem", { name: "Skills" }));
    expect(
      await screen.findByRole("dialog", { name: "Skills history" }),
    ).toBeVisible();
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: "More" }));
    await user.click(
      await screen.findByRole("menuitem", { name: "Classic editor" }),
    );
    await waitFor(() => {
      expect(location()).toBe("/profile?tab=identity");
    });
    expect(screen.getAllByRole("tab")).toHaveLength(6);
    await user.click(
      screen.getByRole("link", { name: "Back to your profile page" }),
    );
    expect(
      await screen.findByRole("list", { name: "Ready for" }),
    ).toBeVisible();
  });
});

describe("About you", () => {
  it("renders skills, facts and answers, with a contents list and tables", async () => {
    await openPage();
    const contents = screen.getByRole("navigation", {
      name: "Parts of About you",
    });
    expect(
      within(contents)
        .getAllByRole("link")
        .map((link) => link.textContent),
    ).toEqual(["Skills", "Projects", "Answers"]);
    expect(
      within(contents).getByRole("link", { name: "Answers" }),
    ).toHaveAttribute("href", "#about-part-2");
    // An underscore inside a word stays a character.
    expect(
      within(part("Skills")).getByText("data_tools: Python, SQL, dbt"),
    ).toBeVisible();
    const projects = part("Projects");
    expect(
      within(projects).getByRole("columnheader", { name: "Project" }),
    ).toBeVisible();
    expect(
      within(projects).getByRole("cell", { name: "Streaming platform" }),
    ).toBeVisible();
    expect(
      within(part("Answers")).getByText("Yes, I consent to background checks."),
    ).toBeVisible();
  });

  it("edits a part in place with a live preview, saves as typed, and Done shows what the app understood", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const { user, about } = await openPage();
    await user.click(screen.getByRole("button", { name: "Edit Skills" }));
    const skills = part("Skills");
    const text = within(skills).getByLabelText("Text");
    expect(within(skills).getByLabelText("Heading")).toHaveValue("Skills");
    expect(
      within(skills).getByText(
        "Done reads this part with your model key: about USD 0.01 to USD 0.03, at list prices.",
      ),
    ).toBeVisible();
    await user.type(text, "\n- Airflow");
    const preview = within(skills).getByRole("region", { name: "Preview" });
    expect(within(preview).getByText("Airflow")).toBeVisible();
    // Saved a second after the last key, the whole document with the part changed.
    await waitFor(
      () => {
        expect(about.saves).toHaveLength(1);
      },
      { timeout: 3000 },
    );
    expect(about.saves[0]).toEqual({
      version: 0,
      text: ABOUT_TEXT.replace(
        "- data_tools: Python, SQL, dbt",
        "- data_tools: Python, SQL, dbt\n- Airflow",
      ),
    });
    expect(await within(skills).findByText("Saved")).toBeVisible();

    await user.click(within(skills).getByRole("button", { name: "Done" }));
    expect(about.dones).toEqual([{ heading: "Skills", version: 1 }]);
    expect(await within(skills).findByText("Reading this part")).toBeVisible();
    expect(within(skills).queryByLabelText("Text")).toBeNull();
    about.read(
      ["1 skill added to what I have: Airflow"],
      ["1 term of data_tools: not written in this part."],
    );
    await act(() => vi.advanceTimersByTimeAsync(READING_POLL_MS));
    const understood = await within(skills).findByRole("region", {
      name: "What the app understood",
    });
    expect(
      within(understood).getByText("1 skill added to what I have: Airflow"),
    ).toBeVisible();
    expect(
      within(understood).getByText(
        "1 term of data_tools: not written in this part.",
      ),
    ).toBeVisible();
    await user.click(within(understood).getByRole("button", { name: "Apply" }));
    expect(about.steps).toEqual(["apply"]);
    expect(
      await within(skills).findByText("Applied to your profile:"),
    ).toBeVisible();
    await user.click(within(skills).getByRole("button", { name: "Undo" }));
    expect(about.steps).toEqual(["apply", "undo"]);
    await waitFor(() => {
      expect(within(skills).queryByText("Applied to your profile:")).toBeNull();
    });
  });

  it("lists a consent change on its own line, applied only by Apply", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const { user, about } = await openPage();
    await user.click(screen.getByRole("button", { name: "Edit Answers" }));
    const answers = part("Answers");
    await user.click(within(answers).getByRole("button", { name: "Done" }));
    expect(await within(answers).findByText("Reading this part")).toBeVisible();
    about.read(
      ["Background checks: was no, now yes"],
      [
        "Not changed: the text does not say clearly whether you can work hybrid. Start the line with Yes or No.",
      ],
    );
    await act(() => vi.advanceTimersByTimeAsync(READING_POLL_MS));
    const understood = await within(answers).findByRole("region", {
      name: "What the app understood",
    });
    expect(
      within(understood).getByText("Background checks: was no, now yes"),
    ).toBeVisible();
    expect(within(understood).getByText(/^Not changed: /)).toBeVisible();
    expect(about.steps).toEqual([]);
    await user.click(
      within(understood).getByRole("button", { name: "Not now" }),
    );
    expect(about.steps).toEqual(["discard"]);
  });

  it("without a model key the text still saves and a line under Done says what is missing", async () => {
    const { user, about } = await openPage({ key: null });
    await user.click(screen.getByRole("button", { name: "Edit Answers" }));
    const answers = part("Answers");
    expect(within(answers).queryByText(/at list prices/)).toBeNull();
    expect(
      within(answers).getByText(
        "The app needs a model key to update your profile from this text.",
      ),
    ).toBeVisible();
    expect(
      within(answers).getByRole("link", {
        name: "Add one on the Account page",
      }),
    ).toHaveAttribute("href", "/account");
    await user.type(within(answers).getByLabelText("Text"), " Or two.");
    await user.click(within(answers).getByRole("button", { name: "Done" }));
    // Done saves at once and closes the part; no reading is asked without a key.
    expect(about.saves).toHaveLength(1);
    expect(about.dones).toEqual([]);
    expect(within(answers).queryByLabelText("Text")).toBeNull();
    expect(
      await within(answers).findByText(
        "Your profile does not have this text yet.",
      ),
    ).toBeVisible();
  });

  it("saves a fact part to the profile at Done, with or without a key, and Undo after it", async () => {
    const { user, about } = await openPage({ key: null });
    await user.click(screen.getByRole("button", { name: "Edit Projects" }));
    const projects = part("Projects");
    expect(within(projects).queryByText(/model key/)).toBeNull();
    await user.type(within(projects).getByLabelText("Text"), "\n- Search");
    await user.click(within(projects).getByRole("button", { name: "Done" }));
    expect(about.dones).toEqual([{ heading: "Projects", version: 1 }]);
    expect(
      await within(projects).findByText("Applied to your profile:"),
    ).toBeVisible();
    expect(
      within(projects).getByText("1 fact block updated: Projects"),
    ).toBeVisible();
    expect(
      within(projects).getByRole("button", { name: "Undo" }),
    ).toBeVisible();
  });

  it("says nothing about money on a subscription", async () => {
    const { user } = await openPage({ key: "subscription" });
    await user.click(screen.getByRole("button", { name: "Edit Skills" }));
    const skills = part("Skills");
    expect(within(skills).queryByText(/at list prices/)).toBeNull();
    expect(within(skills).queryByText(/model key/)).toBeNull();
    expect(within(skills).getByRole("button", { name: "Done" })).toBeEnabled();
  });

  it("asks before keeping a text another tab saved first", async () => {
    const { user, about } = await openPage();
    about.about = { ...about.about, version: 3 };
    await user.click(screen.getByRole("button", { name: "Edit Skills" }));
    const skills = part("Skills");
    await user.type(within(skills).getByLabelText("Text"), "!");
    await user.click(within(skills).getByRole("button", { name: "Done" }));
    expect(
      await screen.findByText("Changed in another tab", { selector: "div" }),
    ).toBeVisible();
    expect(about.dones).toEqual([]);
    await user.click(screen.getByRole("button", { name: "Keep mine" }));
    await waitFor(() => {
      expect(about.saves.at(-1)?.version).toBe(3);
    });
  });

  it("refuses Done without a heading", async () => {
    const { user } = await openPage();
    await user.click(screen.getByRole("button", { name: "Edit Projects" }));
    const projects = part("Projects");
    await user.clear(within(projects).getByLabelText("Heading"));
    expect(
      within(projects).getByText("Give this part a heading."),
    ).toBeVisible();
    expect(
      within(projects).getByRole("button", { name: "Done" }),
    ).toBeDisabled();
  });
});
