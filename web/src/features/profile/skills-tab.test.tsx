import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { toast } from "sonner";
import { afterEach, describe, expect, it } from "vitest";

import { admin, fakeSession } from "@/test/auth";
import { fakeProfile, haraldLike } from "@/test/profile";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import type { Skills } from "./api";

afterEach(() => {
  toast.dismiss();
});

async function openSkills(
  initial = haraldLike() as Parameters<typeof fakeProfile>[0],
) {
  fakeSession(admin);
  const state = fakeProfile(initial);
  const user = userEvent.setup();
  const app = renderApp("/profile?tab=skills");
  await screen.findByRole("form", { name: "Skills" });
  return { user, state, ...app };
}

const save = () => screen.getByRole("button", { name: "Save skills" });
const chips = (name: string) =>
  within(screen.getByRole("list", { name: `Terms of ${name}` }))
    .getAllByRole("listitem")
    .map((li) => li.firstElementChild?.textContent);
const saved = (state: ReturnType<typeof fakeProfile>, index = 0) =>
  state.saves[index]!.body.data as Skills;

describe("skills tab", () => {
  it("shows years, the three lists, terms as chips and gates", async () => {
    await openSkills();
    expect(screen.getByLabelText("Years of experience")).toHaveValue("3.5");
    expect(screen.getByLabelText("Name of have category 1")).toHaveValue(
      "agents",
    );
    expect(chips("agents")).toEqual(["agent", "langgraph"]);
    expect(chips("llm")).toEqual(["\\bllm(?:s)?\\b", "\\bgenai\\b"]);
    expect(
      screen.getByRole("switch", {
        name: "Advanced (regular expressions) for llm",
      }),
    ).toBeChecked();
    expect(
      screen.getByRole("switch", {
        name: "Advanced (regular expressions) for agents",
      }),
    ).not.toBeChecked();
    expect(screen.getByLabelText("Name of adjacent category 1")).toHaveValue(
      "mlops",
    );
    expect(screen.getByLabelText("Name of lacking category 1")).toHaveValue(
      "rust",
    );
    expect(screen.getByLabelText("Name of gate 1")).toHaveValue("phd");
    expect(screen.getByLabelText("Penalty of gate 1")).toHaveValue("25");
    expect(screen.getByText("25 points")).toBeVisible();
  });

  it("adds terms with Enter or the button, refuses empty and duplicate ones, saves", async () => {
    const { user, state } = await openSkills();
    const input = screen.getByLabelText("New term for agents");
    await user.type(input, "  CrewAI {enter}");
    expect(chips("agents")).toEqual(["agent", "langgraph", "CrewAI"]);
    expect(input).toHaveValue("");
    await user.type(input, "Agent");
    await user.click(
      screen.getByRole("button", { name: "Add term to agents" }),
    );
    expect(screen.getByText("This term is already in the list.")).toBeVisible();
    expect(input).toHaveAttribute("aria-invalid", "true");
    await user.clear(input);
    expect(screen.queryByText("This term is already in the list.")).toBeNull();
    await user.type(input, "   {enter}");
    expect(screen.getByText("Enter a term.")).toBeVisible();
    await user.clear(input);
    await user.type(input, "x".repeat(201));
    await user.keyboard("{Enter}");
    expect(
      screen.getByText("A plain term is at most 200 characters."),
    ).toBeVisible();
    await user.clear(input);

    await user.click(screen.getByRole("button", { name: "Remove langgraph" }));
    expect(chips("agents")).toEqual(["agent", "CrewAI"]);
    await user.click(save());
    expect(await screen.findByText("Skills saved.")).toBeVisible();
    expect(saved(state).have[0]).toEqual({
      name: "agents",
      advanced_regex: false,
      terms: ["agent", "CrewAI"],
    });
    // Everything else went back as it came.
    expect(saved(state).gates).toEqual(haraldLike().skills.gates);
    expect(saved(state).years_experience).toBe(3.5);
  });

  it("checks a regular expression before adding it", async () => {
    const { user } = await openSkills();
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

  it("revalidates chips when the advanced switch changes, and blocks the save", async () => {
    const { user, state } = await openSkills();
    await user.type(
      screen.getByLabelText("New term for agents"),
      "(oops{enter}",
    );
    expect(chips("agents")).toContain("(oops");
    const advanced = screen.getByRole("switch", {
      name: "Advanced (regular expressions) for agents",
    });
    await user.click(advanced);
    expect(advanced).toBeChecked();
    const list = screen.getByRole("list", { name: "Terms of agents" });
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
    await user.click(advanced);
    expect(within(list).queryByText(/^Invalid regular expression/)).toBeNull();
    await user.click(save());
    await screen.findByText("Skills saved.");
    expect(saved(state).have[0]!.terms).toContain("(oops");
    expect(saved(state).have[0]!.advanced_regex).toBe(false);
  });

  it("puts the server's regex error, with its position, under the right chip", async () => {
    const { user } = await openSkills();
    server.use(
      http.put("*/api/profile/skills", () =>
        HttpResponse.json(
          {
            detail:
              "data.have.1.terms: term 2: invalid regular expression at position 7: missing ), unterminated subpattern",
          },
          { status: 422 },
        ),
      ),
    );
    await user.type(screen.getByLabelText("Years of experience"), "5");
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

  it("shows a server error for a term it cannot place above the form", async () => {
    const { user } = await openSkills();
    server.use(
      http.put("*/api/profile/skills", () =>
        HttpResponse.json(
          { detail: "data.have.1.terms: term 9: invalid regular expression" },
          { status: 422 },
        ),
      ),
    );
    await user.type(screen.getByLabelText("Years of experience"), "5");
    await user.click(save());
    expect(
      await screen.findByText(
        "The server refused this section: data.have.1.terms: term 9: invalid regular expression",
      ),
    ).toBeVisible();
  });

  it("adds categories, refuses a name used in another list, validates years", async () => {
    const { user, state } = await openSkills();
    await user.click(
      screen.getByRole("button", {
        name: "Add category to adjacent competencies",
      }),
    );
    const name = screen.getByLabelText("Name of adjacent category 2");
    await waitFor(() => {
      expect(name).toHaveFocus();
    });
    await user.type(name, "Agents");
    await user.type(
      screen.getByLabelText("New term for Agents"),
      "autogen{enter}",
    );
    const years = screen.getByLabelText("Years of experience");
    await user.clear(years);
    await user.type(years, "61");
    await user.click(save());
    expect(
      await screen.findByText("Another category already has this name."),
    ).toBeVisible();
    expect(
      screen.getByText(
        "Enter a number of years between 0 and 60, such as 3.5.",
      ),
    ).toBeVisible();
    expect(state.saves).toHaveLength(0);

    await user.clear(name);
    await user.type(name, "orchestration");
    await user.clear(years);
    await user.click(save());
    await screen.findByText("Skills saved.");
    expect(saved(state).years_experience).toBeNull();
    expect(saved(state).adjacent[1]).toEqual({
      name: "orchestration",
      advanced_regex: false,
      terms: ["autogen"],
    });
    expect(
      await screen.findByText(
        /To complete this section, add: years of experience\./,
      ),
    ).toBeVisible();
  });

  it("refuses a category without a name, moves and removes categories", async () => {
    const { user, state } = await openSkills();
    await user.click(
      screen.getByRole("button", {
        name: "Add category to competencies you lack",
      }),
    );
    await user.click(save());
    expect(await screen.findByText("Enter a name.")).toBeVisible();
    await user.click(
      screen.getByRole("button", { name: "Remove lacking category 2" }),
    );
    await user.click(
      screen.getByRole("button", { name: "Move have category 2 up" }),
    );
    expect(screen.getByLabelText("Name of have category 1")).toHaveValue("llm");
    await user.click(save());
    await screen.findByText("Skills saved.");
    expect(saved(state).have.map((c) => c.name)).toEqual(["llm", "agents"]);
    expect(saved(state).none).toHaveLength(1);
  });

  it("adds a gate with a penalty from the slider", async () => {
    const { user, state } = await openSkills();
    await user.click(screen.getByRole("button", { name: "Add gate" }));
    await user.type(screen.getByLabelText("Name of gate 2"), "clearance");
    await user.type(
      screen.getByLabelText("New term for clearance"),
      "security clearance{enter}",
    );
    fireEvent.change(screen.getByLabelText("Penalty of gate 2"), {
      target: { value: "40" },
    });
    expect(screen.getByText("40 points")).toBeVisible();
    await user.click(save());
    await screen.findByText("Skills saved.");
    expect(saved(state).gates[1]).toEqual({
      name: "clearance",
      advanced_regex: false,
      terms: ["security clearance"],
      penalty: 40,
    });
  });

  it("refuses a duplicate gate name", async () => {
    const { user, state } = await openSkills();
    await user.click(screen.getByRole("button", { name: "Add gate" }));
    await user.type(screen.getByLabelText("Name of gate 2"), "PhD");
    await user.click(save());
    expect(
      await screen.findByText("Another gate already has this name."),
    ).toBeVisible();
    expect(state.saves).toHaveLength(0);
  });

  it("Try it: matches a posting against the categories and gates being edited", async () => {
    const { user } = await openSkills();
    // An unsaved term counts too.
    await user.type(
      screen.getByLabelText("New term for mlops"),
      "kubeflow{enter}",
    );
    await user.click(screen.getByLabelText("Posting text"));
    await user.paste(
      "We build Agents with LangGraph and Kubeflow. A PhD is a plus; agent experience required. Rust? No. Reagents, agentic: not counted.",
    );
    const results = screen.getByRole("region", { name: "Matches" });
    const have = within(results).getByRole("list", {
      name: "Competencies you have results",
    });
    const [agents, llm] = within(have).getAllByRole("listitem");
    expect(agents).toHaveTextContent("agentsmatches agents, langgraph, agent");
    expect(llm).toHaveTextContent("llmno match");
    expect(within(results).getByText("(1 of 2 match)")).toBeVisible();
    expect(
      within(results).getByRole("list", {
        name: "Adjacent competencies results",
      }),
    ).toHaveTextContent("mlopsmatches kubeflow");
    expect(
      within(results).getByRole("list", {
        name: "Competencies you lack results",
      }),
    ).toHaveTextContent("rustmatches rust");
    expect(
      within(results).getByRole("list", { name: "Gates results" }),
    ).toHaveTextContent("phdmatches phd");
    // Typing in the box never makes the section dirty.
    expect(screen.getByText("Unsaved changes")).toBeVisible();
  });

  it("Try it counts repeats and uses advanced terms with Python's word rules", async () => {
    const { user } = await openSkills();
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

  it("Try it says when a term cannot be previewed in the browser", async () => {
    const { user } = await openSkills({
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

  it("starts empty for a new user and saves a first category", async () => {
    const { user, state } = await openSkills(null);
    expect(screen.getAllByText("No category yet.")).toHaveLength(3);
    expect(screen.getByText("No gate yet.")).toBeVisible();
    await user.type(screen.getByLabelText("Years of experience"), "2");
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
      years_experience: 2,
      have: [{ name: "python", advanced_regex: false, terms: [] }],
      adjacent: [],
      none: [],
      gates: [],
    });
  });

  it("keeps my edits on a conflict and saves them over the other version", async () => {
    const { user, state } = await openSkills();
    state.revision = 8;
    await user.type(screen.getByLabelText("New term for rust"), "cargo{enter}");
    await user.click(save());
    const dialog = await screen.findByRole("alertdialog", {
      name: "This section changed elsewhere",
    });
    await user.click(
      within(dialog).getByRole("button", { name: "Keep editing" }),
    );
    expect(chips("rust")).toEqual(["rust", "cargo"]);
    await user.click(save());
    await screen.findByText("Skills saved.");
    expect(state.saves.map((s) => s.body.base_revision)).toEqual([6, 8]);
  });

  it("asks before leaving with an unsaved chip", async () => {
    const { user } = await openSkills();
    await user.type(screen.getByLabelText("New term for rust"), "cargo{enter}");
    await user.click(screen.getByRole("tab", { name: /^Search/ }));
    const guard = await screen.findByRole("alertdialog", {
      name: "Discard unsaved changes?",
    });
    await user.click(
      within(guard).getByRole("button", { name: "Discard changes" }),
    );
    expect(await screen.findByRole("form", { name: "Search" })).toBeVisible();
  });
});
