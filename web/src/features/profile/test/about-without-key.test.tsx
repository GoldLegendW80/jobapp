import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";
import { afterEach, describe, expect, it } from "vitest";

import { admin, fakeSession } from "@/test/auth";
import { ABOUT_TEXT, fakeAbout } from "@/test/about";
import { fakeDocuments } from "@/test/documents";
import { fakeProfile, haraldLike } from "@/test/profile";
import { renderApp } from "@/test/render";
import { setSlider } from "@/test/slider";

import type { Answers, Skills } from "../api/profile-api";

afterEach(() => {
  toast.dismiss();
});

/** The profile page of an account with no model key: About you shows its skills and answers as fields. */
async function openPage(
  initial = haraldLike() as Parameters<typeof fakeProfile>[0],
) {
  fakeSession(admin);
  const state = fakeProfile(initial);
  fakeDocuments();
  const about = fakeAbout({ text: ABOUT_TEXT, key: null });
  const user = userEvent.setup();
  const app = renderApp("/profile");
  await screen.findByRole("form", { name: "Skills" }, { timeout: 10_000 });
  return { user, state, about, ...app };
}

const skills = () => screen.getByRole("form", { name: "Skills" });
const answers = () => screen.getByRole("form", { name: "Answers" });

/** Opens the folded groups of `form` named by their titles. */
async function unfold(
  user: ReturnType<typeof userEvent.setup>,
  form: HTMLElement,
  ...titles: string[]
) {
  for (const title of titles)
    await user.click(
      within(form).getByRole("button", { name: new RegExp(`^${title},`) }),
    );
}
const lastSave = (state: ReturnType<typeof fakeProfile>, section: string) =>
  state.saves.filter((save) => save.section === section).at(-1)?.body.data;
const savedSkills = (state: ReturnType<typeof fakeProfile>) =>
  lastSave(state, "skills") as Skills;
const savedAnswers = (state: ReturnType<typeof fakeProfile>) =>
  lastSave(state, "answers") as Answers;

describe("Skills without a model key", () => {
  it("shows the years and the skills in words, each list folded under its names, the expressions left to Advanced matching", async () => {
    const { user } = await openPage();
    const form = skills();
    expect(within(form).getByLabelText("Years of experience")).toHaveValue(
      "3.5",
    );
    expect(within(form).getByText("years")).toBeVisible();
    expect(
      within(form)
        .getAllByRole("button", { expanded: false })
        .map((button) => button.getAttribute("aria-label")),
    ).toEqual([
      "What I have, agents",
      "Close to what I have, mlops",
      "What I do not have, rust",
      "Requirements that lower my fit, phd",
    ]);
    await unfold(
      user,
      form,
      "What I have",
      "Close to what I have",
      "What I do not have",
      "Requirements that lower my fit",
    );
    expect(
      within(form).getByLabelText("Name of category 1 of what I have"),
    ).toHaveValue("agents");
    expect(
      within(form).getByLabelText("Name of category 1 of close to what I have"),
    ).toHaveValue("mlops");
    expect(
      within(form).getByLabelText("Name of category 1 of what I do not have"),
    ).toHaveValue("rust");
    expect(
      within(form).getByLabelText("Name of requirement 1 that lowers my fit"),
    ).toHaveValue("phd");
    // Words only: no switch to regular expressions, and the expression category is not here.
    expect(within(form).queryByRole("switch")).toBeNull();
    expect(within(form).queryByDisplayValue("llm")).toBeNull();
    expect(
      within(
        screen.getByRole("form", { name: "Advanced matching" }),
      ).getByLabelText("Name of have category 1"),
    ).toHaveValue("llm");
  });

  it("saves the years, a new category and a requirement, keeps the expressions, and writes the part again", async () => {
    const { user, state, about } = await openPage();
    const form = skills();
    const years = within(form).getByLabelText("Years of experience");
    await user.clear(years);
    await user.type(years, "4");
    await unfold(user, form, "What I have", "Requirements that lower my fit");
    await user.click(
      within(form).getByRole("button", {
        name: "Add category to what I have",
      }),
    );
    const name = within(form).getByLabelText(
      "Name of category 2 of what I have",
    );
    expect(name).toHaveFocus();
    await user.type(name, "data");
    await user.type(
      within(form).getByLabelText("New term for data"),
      "dbt{enter}",
    );
    await user.click(
      within(form).getByRole("button", {
        name: "Add a requirement that lowers my fit",
      }),
    );
    await user.type(
      within(form).getByLabelText("Name of requirement 2 that lowers my fit"),
      "german",
    );
    await user.type(
      within(form).getByLabelText("New term for german"),
      "fluent german{enter}",
    );
    setSlider(
      within(form).getByLabelText(
        "Points taken off of requirement 2 that lowers my fit",
      ),
      40,
    );
    expect(within(form).getByText("Unsaved changes")).toBeVisible();
    await user.click(within(form).getByRole("button", { name: "Save skills" }));
    expect(await screen.findByText("Skills saved.")).toBeVisible();
    const sent = savedSkills(state);
    const harald = haraldLike().skills;
    expect(sent.years_experience).toBe(4);
    // The expressions first, as stored, then the skills in words.
    expect(sent.have).toEqual([
      harald.have[1],
      harald.have[0],
      { name: "data", advanced_regex: false, terms: ["dbt"] },
    ]);
    expect(sent.gates).toEqual([
      ...harald.gates,
      {
        name: "german",
        advanced_regex: false,
        terms: ["fluent german"],
        penalty: 40,
      },
    ]);
    expect(state.saves.at(-1)?.body.base_revision).toBe(6);
    // The Skills part said what the profile held: it is written again from the profile.
    await waitFor(() => {
      expect(about.rewrites).toEqual([{ heading: "Skills", version: 0 }]);
    });
  });

  it("checks the years and a duplicate name before sending", async () => {
    const { user, state } = await openPage();
    const form = skills();
    const years = within(form).getByLabelText("Years of experience");
    await user.clear(years);
    await user.type(years, "61");
    await unfold(user, form, "What I do not have");
    await user.click(
      within(form).getByRole("button", {
        name: "Add category to what I do not have",
      }),
    );
    await user.type(
      within(form).getByLabelText("Name of category 2 of what I do not have"),
      "Agents",
    );
    // Folded again: the refused name opens its list on Save.
    await unfold(user, form, "What I do not have");
    expect(
      within(form).queryByLabelText("Name of category 2 of what I do not have"),
    ).toBeNull();
    await user.click(within(form).getByRole("button", { name: "Save skills" }));
    expect(
      await within(form).findByText(
        "Enter a number of years between 0 and 60, such as 3.5.",
      ),
    ).toBeVisible();
    expect(
      within(form).getByText("Another category already has this name."),
    ).toBeVisible();
    expect(
      within(form).getByLabelText("Name of category 2 of what I do not have"),
    ).toHaveValue("Agents");
    expect(state.saves).toHaveLength(0);
  });

  it("keeps a part with text the profile does not have, and reads it again", async () => {
    fakeSession(admin);
    const state = fakeProfile();
    fakeDocuments();
    const about = fakeAbout({ text: ABOUT_TEXT, key: null });
    about.about = {
      ...about.about,
      parts: about.about.parts.map((one) =>
        one.key === "skills" ? { ...one, state: "edited" } : one,
      ),
    };
    const user = userEvent.setup();
    renderApp("/profile");
    const form = await screen.findByRole(
      "form",
      { name: "Skills" },
      { timeout: 10_000 },
    );
    const years = within(form).getByLabelText("Years of experience");
    await user.clear(years);
    await user.type(years, "5");
    await user.click(within(form).getByRole("button", { name: "Save skills" }));
    expect(await screen.findByText("Skills saved.")).toBeVisible();
    expect(savedSkills(state).years_experience).toBe(5);
    expect(about.rewrites).toEqual([]);
  });

  it("asks before leaving with unsaved skills, and names them", async () => {
    const { user } = await openPage();
    const years = within(skills()).getByLabelText("Years of experience");
    await user.type(years, "1");
    await user.click(screen.getByRole("link", { name: "Jobs" }));
    const guard = await screen.findByRole("alertdialog", {
      name: "Discard unsaved changes?",
    });
    expect(
      within(guard).getByText(
        "Your changes to Skills are not saved. Keep editing to save them first, or discard them.",
      ),
    ).toBeVisible();
    await user.click(
      within(guard).getByRole("button", { name: "Keep editing" }),
    );
    expect(years).toHaveValue("3.51");
  });

  it("shows the conflict dialog on a stale save and reloads the other version", async () => {
    const { user, state } = await openPage();
    state.sections.skills = {
      ...haraldLike().skills,
      years_experience: 9,
    };
    state.revision = 11;
    const years = within(skills()).getByLabelText("Years of experience");
    await user.clear(years);
    await user.type(years, "4");
    await user.click(
      within(skills()).getByRole("button", { name: "Save skills" }),
    );
    const dialog = await screen.findByRole("alertdialog", {
      name: "This section changed elsewhere",
    });
    await user.click(
      within(dialog).getByRole("button", { name: "Reload (discard mine)" }),
    );
    await waitFor(() => {
      expect(
        within(skills()).getByLabelText("Years of experience"),
      ).toHaveValue("9");
    });
  });
});

/** Opens the groups of the answers named by their titles. */
async function openGroups(
  user: ReturnType<typeof userEvent.setup>,
  ...titles: string[]
) {
  for (const title of titles)
    await user.click(
      within(answers()).getByRole("button", { name: new RegExp(`^${title}`) }),
    );
}

const choice = (group: string, name: string) =>
  within(within(answers()).getByRole("radiogroup", { name: group })).getByRole(
    "radio",
    { name },
  );

describe("Answers without a model key", () => {
  it("folds each group under what it holds, and shows the stored answers with labelled controls", async () => {
    const { user } = await openPage();
    const form = answers();
    expect(
      within(form)
        .getAllByRole("button", { expanded: false })
        .map((button) => button.getAttribute("aria-label")),
    ).toEqual([
      "Work authorisation, France, United Kingdom",
      "Availability and conditions, 5 of 5 answered",
      "About you on forms, 5 of 5 answered",
      "Work setup and consents, 5 of 5 said",
      "Salary, Give my range, 2 ranges",
      "Equal opportunity questions, Use the answers I store here",
      "In your own words, 2 of 2 answered",
      "Leave blank, 1 question",
    ]);
    expect(within(form).queryByLabelText("Preferred name")).toBeNull();
    await openGroups(
      user,
      "Work authorisation",
      "About you on forms",
      "Work setup and consents",
      "Salary",
      "Equal opportunity questions",
    );
    expect(within(form).getByLabelText("Country or region 1")).toHaveValue(
      "France",
    );
    expect(choice("Needs visa sponsorship in country 2", "Yes")).toBeChecked();
    expect(within(form).getByLabelText("Preferred name")).toHaveValue("Ana");
    expect(
      within(form).getByLabelText("Year of the highest degree"),
    ).toHaveValue("2022");
    expect(choice("Willing to work on site", "Yes")).toBeChecked();
    expect(choice("Consent to a background check", "No")).toBeChecked();
    expect(choice("What to do", "Use the answers I store here")).toBeChecked();
    expect(choice("Salary stance", "Give my range")).toBeChecked();
    expect(within(form).getByLabelText("Currency of range 1")).toHaveValue(
      "EUR",
    );
    expect(within(form).getByLabelText("Answer for topic 1")).toHaveValue(
      "Woman",
    );
  });

  it("saves what was typed and chosen, and keeps the rest", async () => {
    const { user, state, about } = await openPage();
    const form = answers();
    await openGroups(
      user,
      "About you on forms",
      "Salary",
      "Work setup and consents",
    );
    const name = within(form).getByLabelText("Preferred name");
    await user.clear(name);
    await user.type(name, "Ani");
    await user.click(choice("Salary stance", "Write my salary policy"));
    await user.click(choice("Consent to a reference check", "Not said"));
    // A click on the choice that is on keeps it on.
    const hybrid = choice("Willing to work hybrid", "Yes");
    await user.click(hybrid);
    expect(hybrid).toBeChecked();
    expect(
      within(form).getByRole("button", {
        name: "Salary, Write my salary policy, 2 ranges",
      }),
    ).toBeVisible();
    await user.click(
      within(form).getByRole("button", { name: "Save answers" }),
    );
    expect(await screen.findByText("Answers saved.")).toBeVisible();
    const body = savedAnswers(state);
    expect(body.preferred_name).toBe("Ani");
    expect(body.salary_stance).toBe("sentence");
    expect(body.reference_check_consent).toBeNull();
    expect(body.willing_hybrid).toBe(true);
    expect(body.salary_ranges).toHaveLength(2);
    expect(body.eeo_answers).toEqual([{ topic: "gender", answer: "Woman" }]);
    expect(body.eeo_policy).toBe("stored");
    expect(body.work_authorization).toEqual(
      haraldLike().answers.work_authorization,
    );
    await waitFor(() => {
      expect(about.rewrites).toEqual([{ heading: "Answers", version: 0 }]);
    });
  });

  it("adds a salary range from the keyboard and refuses a bad currency", async () => {
    const { user, state } = await openPage();
    const form = answers();
    await openGroups(user, "Salary");
    await user.click(within(form).getByRole("button", { name: "Add range" }));
    const currency = within(form).getByLabelText("Currency of range 3");
    expect(currency).toHaveFocus();
    await user.type(currency, "chf");
    await user.type(within(form).getByLabelText("Minimum of range 3"), "90000");
    expect(within(form).getAllByText("CHF")).toHaveLength(2);
    await user.click(
      within(form).getByRole("button", { name: "Save answers" }),
    );
    expect(
      await within(form).findByText(/three capital letters/),
    ).toBeVisible();
    expect(state.saves).toHaveLength(0);
  });

  it("opens the group of a refused field, even folded", async () => {
    const { user, state } = await openPage();
    const form = answers();
    await openGroups(user, "About you on forms");
    const year = within(form).getByLabelText("Year of the highest degree");
    await user.clear(year);
    await user.type(year, "1800");
    await openGroups(user, "About you on forms");
    expect(
      within(form).queryByLabelText("Year of the highest degree"),
    ).toBeNull();
    await user.click(
      within(form).getByRole("button", { name: "Save answers" }),
    );
    expect(
      await within(form).findByText("Enter a year between 1950 and 2100."),
    ).toBeVisible();
    expect(
      within(form).getByLabelText("Year of the highest degree"),
    ).toHaveValue("1800");
    expect(state.saves).toHaveLength(0);
  });

  it("shows the stored equal opportunity answers only under the stored policy", async () => {
    const { user } = await openPage();
    const form = answers();
    await openGroups(user, "Equal opportunity questions");
    expect(within(form).getByLabelText("Answer for topic 1")).toHaveValue(
      "Woman",
    );
    await user.click(choice("What to do", "Decline to answer"));
    expect(within(form).queryByLabelText("Answer for topic 1")).toBeNull();
    expect(
      within(form).queryByRole("button", { name: "Add answer" }),
    ).toBeNull();
  });

  it("adds a country, moves it up and saves the countries in that order", async () => {
    const { user, state } = await openPage();
    const form = answers();
    await openGroups(user, "Work authorisation");
    await user.click(within(form).getByRole("button", { name: "Add country" }));
    const country = within(form).getByLabelText("Country or region 3");
    expect(country).toHaveFocus();
    await user.type(country, "Spain");
    await user.type(
      within(form).getByLabelText("Answer for country 3"),
      "EU citizen.",
    );
    await user.click(
      within(form).getByRole("button", { name: "Move country 3 up" }),
    );
    await user.click(
      within(form).getByRole("button", { name: "Save answers" }),
    );
    await screen.findByText("Answers saved.");
    expect(
      savedAnswers(state).work_authorization.map((one) => one.country),
    ).toEqual(["France", "Spain", "United Kingdom"]);
  });

  it("opens a new account with empty answers and saves a first one", async () => {
    const { user, state } = await openPage(null);
    const form = answers();
    expect(
      within(form).getByRole("button", {
        name: "Work authorisation, No country yet",
      }),
    ).toBeVisible();
    await openGroups(user, "Work setup and consents", "Availability");
    expect(choice("Willing to work on site", "Not said")).toBeChecked();
    await user.type(within(form).getByLabelText("Notice period"), "One month.");
    await user.click(
      within(form).getByRole("button", { name: "Save answers" }),
    );
    await screen.findByText("Answers saved.");
    expect(savedAnswers(state).notice_period).toBe("One month.");
    expect(state.saves.at(-1)?.body.base_revision).toBe(0);
  });
});
