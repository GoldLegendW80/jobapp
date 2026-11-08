import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import { admin, fakeSession } from "@/test/auth";
import { ABOUT_TEXT, aboutOf, aboutPartTitles, fakeAbout } from "@/test/about";
import { fakeDocuments } from "@/test/documents";
import { fakeProfile } from "@/test/profile";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import { READING_POLL_MS } from "../api/about-api";

afterEach(() => {
  vi.useRealTimers();
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

const part = (name: string) => screen.getByRole("region", { name });

describe("About you", () => {
  it("renders skills, facts and answers, with tables", async () => {
    await openPage();
    expect(aboutPartTitles()).toEqual(["Skills", "Projects", "Answers"]);
    expect(document.getElementById("about-part-2")).toHaveTextContent(
      "Answers",
    );
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

  it("without a model key shows Skills and Answers as fields, and keeps the fact parts as text", async () => {
    await openPage({ key: null });
    // The fields stand where their parts are, at the ids links point at.
    expect(aboutPartTitles()).toEqual(["Skills", "Projects", "Answers"]);
    expect(document.getElementById("about-skills")).toHaveTextContent("Skills");
    expect(document.getElementById("about-answers")).toHaveTextContent(
      "Answers",
    );
    expect(screen.getByRole("form", { name: "Skills" })).toBeVisible();
    expect(screen.getByRole("form", { name: "Answers" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Edit Skills" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Edit Answers" })).toBeNull();
    expect(
      within(part("Skills")).getByRole("link", {
        name: "Add one on the Account page",
      }),
    ).toHaveAttribute("href", "/account");
    expect(screen.getByRole("button", { name: "Edit Projects" })).toBeVisible();
    expect(document.getElementById("about-skills")).toBe(part("Skills"));
    expect(document.getElementById("about-answers")).toBe(part("Answers"));
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

describe("the expressions About you leaves out", () => {
  it("says under the Skills part that they stay and keep counting", async () => {
    fakeSession(admin);
    fakeProfile();
    fakeDocuments();
    fakeAbout();
    server.use(
      http.get("*/api/profile/about", () =>
        HttpResponse.json({ ...aboutOf(ABOUT_TEXT), advanced: 2 }),
      ),
    );
    renderApp("/profile/about");
    const skills = await screen.findByRole("region", { name: "Skills" });
    expect(
      within(skills).getByText(
        "2 advanced categories (regular expressions) stay as they are and keep counting in the fit score.",
      ),
    ).toBeVisible();
    expect(within(skills).queryByRole("link")).toBeNull();
  });
});
