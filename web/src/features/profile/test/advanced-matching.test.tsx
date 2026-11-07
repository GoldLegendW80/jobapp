import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { toast } from "sonner";
import { afterEach, describe, expect, it } from "vitest";

import { fakeAbout } from "@/test/about";
import { admin, fakeSession } from "@/test/auth";
import { fakeDocuments } from "@/test/documents";
import { fakeProfile, haraldLike } from "@/test/profile";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";
import { setSlider } from "@/test/slider";

import { profileKey, type Skills } from "../api/profile-api";

afterEach(() => {
  toast.dismiss();
});

/** Harald's skills with a regular expression in each list and a requirement written as one. */
function withExpressions(): Parameters<typeof fakeProfile>[0] {
  const harald = haraldLike();
  return {
    ...harald,
    skills: {
      ...harald.skills,
      have: [
        ...harald.skills.have,
        { name: "vision", advanced_regex: true, terms: ["\\bvision\\b"] },
      ],
      adjacent: [
        ...harald.skills.adjacent,
        { name: "pipelines", advanced_regex: true, terms: ["\\bkube\\w*\\b"] },
      ],
      gates: [
        ...harald.skills.gates,
        {
          name: "clearance",
          advanced_regex: true,
          terms: ["\\bclearance\\b"],
          penalty: 30,
        },
      ],
    },
  };
}

async function openMatching(
  initial = haraldLike() as Parameters<typeof fakeProfile>[0],
) {
  fakeSession(admin);
  const state = fakeProfile(initial);
  fakeDocuments();
  // With a model key About you holds the skills in words as text, not as fields.
  fakeAbout({ key: "api_key" });
  const user = userEvent.setup();
  const app = renderApp("/profile");
  const form = await screen.findByRole("form", { name: "Advanced matching" });
  return { user, state, form, ...app };
}

const save = () =>
  screen.getByRole("button", { name: "Save advanced matching" });
const chips = (name: string) =>
  within(screen.getByRole("list", { name: `Terms of ${name}` }))
    .getAllByRole("listitem")
    .map((li) => li.firstElementChild?.textContent);
const saved = (state: ReturnType<typeof fakeProfile>, index = 0) =>
  state.saves[index]!.body.data as Skills;

describe("advanced matching", () => {
  it("holds only the categories and requirements written as regular expressions", async () => {
    const { form } = await openMatching(withExpressions());
    expect(within(form).getByLabelText("Name of have category 1")).toHaveValue(
      "llm",
    );
    expect(within(form).getByLabelText("Name of have category 2")).toHaveValue(
      "vision",
    );
    expect(chips("llm")).toEqual(["\\bllm(?:s)?\\b", "\\bgenai\\b"]);
    expect(
      within(form).getByRole("switch", {
        name: "Advanced (regular expressions) for llm",
      }),
    ).toBeChecked();
    expect(
      within(form).getByLabelText("Name of adjacent category 1"),
    ).toHaveValue("pipelines");
    expect(within(form).getByLabelText("Name of requirement 1")).toHaveValue(
      "clearance",
    );
    expect(
      within(form).getByLabelText("Points taken off of requirement 1"),
    ).toHaveAttribute("aria-valuenow", "30");
    // The categories in words and the years are About you's.
    expect(within(form).queryByDisplayValue("agents")).toBeNull();
    expect(within(form).queryByDisplayValue("phd")).toBeNull();
    expect(within(form).queryByLabelText("Years of experience")).toBeNull();
    expect(within(form).getByText("No category yet.")).toBeVisible();
  });

  it("adds a term, refuses a duplicate, and saves with the skills in words as stored", async () => {
    const { user, state } = await openMatching();
    const input = screen.getByLabelText("New term for llm");
    await user.type(input, "\\bclaude\\b{enter}");
    expect(chips("llm")).toEqual([
      "\\bllm(?:s)?\\b",
      "\\bgenai\\b",
      "\\bclaude\\b",
    ]);
    await user.type(input, "\\bGENAI\\b{enter}");
    expect(screen.getByText("This term is already in the list.")).toBeVisible();
    await user.clear(input);
    await user.click(
      screen.getByRole("button", { name: "Remove \\bgenai\\b" }),
    );
    await user.click(save());
    expect(await screen.findByText("Skills saved.")).toBeVisible();
    // The expressions first, then the categories in words and the years, as they came.
    expect(saved(state)).toEqual({
      ...haraldLike().skills,
      have: [
        {
          name: "llm",
          advanced_regex: true,
          terms: ["\\bllm(?:s)?\\b", "\\bclaude\\b"],
        },
        haraldLike().skills.have[0],
      ],
    });
    expect(state.saves[0]!.body.base_revision).toBe(6);
  });

  it("checks a regular expression before adding it", async () => {
    const { user } = await openMatching();
    const input = screen.getByLabelText("New term for llm");
    await user.type(input, "(a+)+{enter}");
    expect(
      screen.getByText(
        "Nested quantifiers such as (a+)+ are not allowed: they can make matching hang.",
      ),
    ).toBeVisible();
    await user.clear(input);
    await user.type(input, "(abc{enter}");
    expect(
      screen.getByText(/^Invalid regular expression: .+\.$/),
    ).toBeVisible();
    expect(chips("llm")).toHaveLength(2);
    await user.clear(input);
    await user.type(input, "\\bcl(?:au)de\\b{enter}");
    expect(chips("llm")).toEqual([
      "\\bllm(?:s)?\\b",
      "\\bgenai\\b",
      "\\bcl(?:au)de\\b",
    ]);
  });

  it("refuses numbered backreferences and group names already used in the list", async () => {
    const { user } = await openMatching();
    const input = screen.getByLabelText("New term for llm");
    await user.type(input, "(a)\\1{enter}");
    expect(
      screen.getByText(/^Numbered backreferences such as \\1 are not allowed/),
    ).toBeVisible();
    await user.clear(input);
    await user.type(input, "(?P<w>gpt)(?P=w){enter}");
    expect(chips("llm")).toHaveLength(3);
    await user.clear(input);
    await user.type(input, "(?P<w>claude){enter}");
    expect(
      screen.getByText(
        'The group name "w" is already used by term 3: group names must be unique across the list.',
      ),
    ).toBeVisible();
    expect(chips("llm")).toHaveLength(3);
  });

  it("revalidates chips when the switch changes, and blocks the save", async () => {
    const { user, state } = await openMatching();
    const advanced = screen.getByRole("switch", {
      name: "Advanced (regular expressions) for llm",
    });
    await user.click(
      within(advanced.parentElement!).getByText(
        "Advanced (regular expressions)",
      ),
    );
    expect(advanced).not.toBeChecked();
    await user.type(screen.getByLabelText("New term for llm"), "(oops{enter}");
    expect(chips("llm")).toContain("(oops");
    await user.click(advanced);
    expect(advanced).toBeChecked();
    const list = screen.getByRole("list", { name: "Terms of llm" });
    expect(
      within(list).getByText(/^Invalid regular expression: /),
    ).toBeVisible();
    await user.click(save());
    await waitFor(() => {
      expect(
        within(list).getAllByText(/^Invalid regular expression: /).length,
      ).toBeGreaterThan(0);
    });
    expect(state.saves).toHaveLength(0);
  });

  it("sends a category switched to words to About you after the save", async () => {
    const { user, state, form } = await openMatching();
    await user.click(
      screen.getByRole("switch", {
        name: "Advanced (regular expressions) for llm",
      }),
    );
    await user.click(save());
    await screen.findByText("Skills saved.");
    expect(saved(state).have[0]).toEqual({
      name: "llm",
      advanced_regex: false,
      terms: ["\\bllm(?:s)?\\b", "\\bgenai\\b"],
    });
    await waitFor(() => {
      expect(within(form).queryByDisplayValue("llm")).toBeNull();
    });
    expect(within(form).getAllByText("No category yet.")).toHaveLength(3);
  });

  it("puts the server's regex error, with its position, under the right chip", async () => {
    const { user } = await openMatching();
    server.use(
      http.put("*/api/profile/skills", () =>
        HttpResponse.json(
          {
            detail:
              "data.have.0.terms: term 2: invalid regular expression at position 7: missing ), unterminated subpattern",
          },
          { status: 422 },
        ),
      ),
    );
    await user.click(save());
    const list = screen.getByRole("list", { name: "Terms of llm" });
    const second = within(list).getAllByRole("listitem")[1]!;
    expect(
      await within(second).findByText(
        "Invalid regular expression at position 7: missing ), unterminated subpattern.",
      ),
    ).toBeVisible();
    expect(
      within(within(list).getAllByRole("listitem")[0]!).queryByRole("alert"),
    ).toBeNull();
    expect(
      within(second).getByText("\\bgenai\\b").parentElement,
    ).toHaveAttribute("aria-invalid", "true");
    // Removing a chip clears the server's errors, which are kept by position.
    await user.click(
      screen.getByRole("button", { name: "Remove \\bllm(?:s)?\\b" }),
    );
    expect(within(list).queryByRole("alert")).toBeNull();
  });

  it("shows a server error it cannot place above the form", async () => {
    const { user } = await openMatching();
    server.use(
      http.put("*/api/profile/skills", () =>
        HttpResponse.json(
          { detail: "data.have.0.terms: term 9: invalid regular expression" },
          { status: 422 },
        ),
      ),
    );
    await user.click(save());
    expect(
      await screen.findByText(
        "The server refused this section: data.have.0.terms: term 9: invalid regular expression",
      ),
    ).toBeVisible();
  });

  it("adds a category as a regular expression and refuses a name used in another list", async () => {
    const { user, state } = await openMatching();
    await user.click(
      screen.getByRole("button", {
        name: "Add category to adjacent competencies",
      }),
    );
    const name = screen.getByLabelText("Name of adjacent category 1");
    await waitFor(() => {
      expect(name).toHaveFocus();
    });
    await user.type(name, "LLM");
    expect(
      screen.getByRole("switch", {
        name: "Advanced (regular expressions) for LLM",
      }),
    ).toBeChecked();
    await user.type(
      screen.getByLabelText("New term for LLM"),
      "\\bautogen\\b{enter}",
    );
    await user.click(save());
    expect(
      await screen.findByText("Another category already has this name."),
    ).toBeVisible();
    expect(state.saves).toHaveLength(0);

    await user.clear(name);
    await user.type(name, "orchestration");
    await user.click(save());
    await screen.findByText("Skills saved.");
    expect(saved(state).adjacent).toEqual([
      { name: "orchestration", advanced_regex: true, terms: ["\\bautogen\\b"] },
      ...haraldLike().skills.adjacent,
    ]);
  });

  it("refuses a category without a name, moves and removes categories", async () => {
    const { user, state } = await openMatching(withExpressions());
    await user.click(
      screen.getByRole("button", {
        name: "Add category to competencies you lack",
      }),
    );
    await user.click(save());
    expect(await screen.findByText("Enter a name.")).toBeVisible();
    await user.click(
      screen.getByRole("button", { name: "Remove lacking category 1" }),
    );
    await user.click(
      screen.getByRole("button", { name: "Move have category 2 up" }),
    );
    expect(screen.getByLabelText("Name of have category 1")).toHaveValue(
      "vision",
    );
    await user.click(save());
    await screen.findByText("Skills saved.");
    expect(saved(state).have.map((c) => c.name)).toEqual([
      "vision",
      "llm",
      "agents",
    ]);
    expect(saved(state).none).toEqual(haraldLike().skills.none);
  });

  it("adds a requirement with a penalty from the slider", async () => {
    const { user, state } = await openMatching();
    await user.click(screen.getByRole("button", { name: "Add a requirement" }));
    await user.type(
      screen.getByLabelText("Name of requirement 1"),
      "clearance",
    );
    await user.type(
      screen.getByLabelText("New term for clearance"),
      "\\bsecurity clearance\\b{enter}",
    );
    setSlider(screen.getByLabelText("Points taken off of requirement 1"), 40);
    expect(screen.getByText("40 points")).toBeVisible();
    await user.click(save());
    await screen.findByText("Skills saved.");
    expect(saved(state).gates).toEqual([
      {
        name: "clearance",
        advanced_regex: true,
        terms: ["\\bsecurity clearance\\b"],
        penalty: 40,
      },
      ...haraldLike().skills.gates,
    ]);
  });

  it("refuses a duplicate requirement name", async () => {
    const { user, state } = await openMatching(withExpressions());
    await user.click(screen.getByRole("button", { name: "Add a requirement" }));
    await user.type(
      screen.getByLabelText("Name of requirement 2"),
      "Clearance",
    );
    await user.click(save());
    expect(
      await screen.findByText("Another requirement already has this name."),
    ).toBeVisible();
    expect(state.saves).toHaveLength(0);
  });

  it("starts empty for a new user and saves a first category", async () => {
    const { user, state } = await openMatching(null);
    expect(screen.getAllByText("No category yet.")).toHaveLength(3);
    expect(screen.getByText("None yet.")).toBeVisible();
    await user.click(
      screen.getByRole("button", {
        name: "Add category to competencies you have",
      }),
    );
    await user.type(screen.getByLabelText("Name of have category 1"), "python");
    expect(screen.getByText("No term yet.")).toBeVisible();
    await user.click(save());
    await screen.findByText("Skills saved.");
    expect(saved(state)).toEqual({
      years_experience: null,
      have: [{ name: "python", advanced_regex: true, terms: [] }],
      adjacent: [],
      none: [],
      gates: [],
    });
  });

  it("keeps what About you applied meanwhile, without a conflict", async () => {
    const { user, state, client } = await openMatching();
    // About you's Apply writes the skills in words; the page reads the profile again.
    const words: Skills = {
      ...haraldLike().skills,
      years_experience: 5,
      have: [
        haraldLike().skills.have[1]!,
        { name: "data", advanced_regex: false, terms: ["sql", "dbt"] },
      ],
    };
    state.sections.skills = words;
    state.revision += 1;
    await client.invalidateQueries({ queryKey: profileKey });
    await user.type(
      screen.getByLabelText("New term for llm"),
      "\\bgpt\\b{enter}",
    );
    await user.click(save());
    await screen.findByText("Skills saved.");
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(state.saves[0]!.body.base_revision).toBe(7);
    expect(saved(state)).toEqual({
      ...words,
      have: [
        {
          name: "llm",
          advanced_regex: true,
          terms: ["\\bllm(?:s)?\\b", "\\bgenai\\b", "\\bgpt\\b"],
        },
        { name: "data", advanced_regex: false, terms: ["sql", "dbt"] },
      ],
    });
  });

  it("keeps my edits on a conflict and saves them over the other version", async () => {
    const { user, state } = await openMatching();
    state.revision = 8;
    await user.type(
      screen.getByLabelText("New term for llm"),
      "\\bgpt\\b{enter}",
    );
    await user.click(save());
    const dialog = await screen.findByRole("alertdialog", {
      name: "This section changed elsewhere",
    });
    await user.click(
      within(dialog).getByRole("button", { name: "Keep editing" }),
    );
    expect(chips("llm")).toEqual([
      "\\bllm(?:s)?\\b",
      "\\bgenai\\b",
      "\\bgpt\\b",
    ]);
    await user.click(save());
    await screen.findByText("Skills saved.");
    expect(state.saves.map((s) => s.body.base_revision)).toEqual([6, 8]);
  });

  it("asks before leaving with an unsaved chip", async () => {
    const { user } = await openMatching();
    await user.type(
      screen.getByLabelText("New term for llm"),
      "\\bgpt\\b{enter}",
    );
    await user.click(screen.getByRole("link", { name: "Admin" }));
    expect(
      await screen.findByRole("alertdialog", {
        name: "Discard unsaved changes?",
      }),
    ).toHaveTextContent("Your changes to Advanced matching are not saved.");
  });
});

describe("try a posting", () => {
  it("matches a posting against the skills in words and the expressions being edited", async () => {
    const { user } = await openMatching(withExpressions());
    // An unsaved expression counts too.
    await user.type(
      screen.getByLabelText("New term for pipelines"),
      "\\bkubeflow\\b{enter}",
    );
    await user.click(screen.getByLabelText("Posting text"));
    await user.paste(
      "We build Agents with LangGraph and Kubeflow. A PhD is a plus; agent experience required. Rust? No. Reagents, agentic: not counted.",
    );
    const results = screen.getByRole("region", { name: "Matches" });
    const have = within(results).getByRole("list", {
      name: "Competencies you have results",
    });
    const [agents, llm, vision] = within(have).getAllByRole("listitem");
    expect(agents).toHaveTextContent("agentsmatches agents, langgraph, agent");
    expect(llm).toHaveTextContent("llmno match");
    expect(vision).toHaveTextContent("visionno match");
    expect(within(results).getByText("(1 of 3 match)")).toBeVisible();
    expect(
      within(results).getByRole("list", {
        name: "Adjacent competencies results",
      }),
    ).toHaveTextContent("pipelinesmatches kubeflow");
    expect(
      within(results).getByRole("list", {
        name: "Competencies you lack results",
      }),
    ).toHaveTextContent("rustmatches rust");
    expect(
      within(results).getByRole("list", {
        name: "Requirements you do not meet results",
      }),
    ).toHaveTextContent("phdmatches phd");
    // The posting box never makes the form dirty; the term added above does.
    expect(screen.getAllByText("Unsaved changes")).toHaveLength(1);
  });

  it("counts repeats and uses expressions with Python's word rules", async () => {
    const { user } = await openMatching();
    await user.click(screen.getByLabelText("Posting text"));
    await user.paste("LLMs, LLM, GenAI. Not genaix. Une IA générative.");
    const have = screen.getByRole("list", {
      name: "Competencies you have results",
    });
    expect(within(have).getAllByRole("listitem")[1]).toHaveTextContent(
      "llmmatches llms, llm, genai",
    );
    await user.clear(screen.getByLabelText("Posting text"));
    expect(screen.queryByRole("region", { name: "Matches" })).toBeNull();
    await user.type(
      screen.getByLabelText("Posting text"),
      "agent agent agents",
    );
    expect(
      within(
        screen.getByRole("list", {
          name: "Competencies you have results",
        }),
      ).getAllByRole("listitem")[0],
    ).toHaveTextContent("matches agent (2), agents");
  });

  it("says when a term cannot be previewed in the browser", async () => {
    const { user } = await openMatching({
      ...haraldLike(),
      skills: {
        ...haraldLike().skills,
        have: [{ name: "possessive", advanced_regex: true, terms: ["a++b"] }],
        adjacent: [{ name: "empty", advanced_regex: false, terms: [] }],
      },
    });
    await user.click(screen.getByLabelText("Posting text"));
    await user.paste("aab");
    expect(
      screen.getByRole("list", { name: "Competencies you have results" }),
    ).toHaveTextContent(
      "possessivecannot be previewed here (Python only syntax); the server will still use it",
    );
    expect(
      screen.getByRole("list", { name: "Adjacent competencies results" }),
    ).toHaveTextContent("emptyno terms");
  });
});
