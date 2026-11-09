import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { admin, fakeSession, member } from "@/test/auth";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";
import { crumb } from "@/test/breadcrumb";

import {
  changesOf,
  REVERT_NOTE,
  type AgentCategory,
  type AgentPage,
  type AgentSummary,
  type Editable,
} from "../api/agents-api";

const CODE: Editable = {
  description: "Searches one job website with the browser, as a person would.",
  system_prompt: "You search one job website.\nReport what you verified.",
  default_message: "",
  model: "claude-sonnet-5-5",
  model_by_credential: { anthropic_api_key: "claude-sonnet-5-5" },
  effort: "low",
  max_turns: 10000,
  wall_time_s: 86400,
  tools: ["browser_snapshot", "report_postings"],
};

function makePage(overrides: Partial<AgentPage> = {}): AgentPage {
  return {
    name: "browser_discovery",
    module: "app.browser.finder",
    starter: "A job search sweep, one website or careers page at a time",
    flags: [
      "Private: its inputs and outputs are never stored, only their sizes and counts.",
    ],
    test_only: false,
    internal: true,
    private_io: true,
    code: CODE,
    saved: {},
    effective: CODE,
    input_fields: [
      {
        name: "request",
        type: "string",
        required: true,
        default: null,
        limits: "at most 64 characters",
        description: "",
      },
    ],
    input_schema: {
      type: "object",
      properties: { request: { type: "string" } },
    },
    first_message: "For a website search, a JSON object search_scope.",
    uses_default_message: false,
    tools: [
      {
        name: "browser_snapshot",
        full_name: "mcp__app__browser_snapshot",
        module: "app.browser.finder_tools",
        description: "Read the page as an accessibility snapshot.",
        input_schema: { type: "object", properties: {} },
        described_at: "2026-11-04T10:00:00Z",
        on: true,
      },
      {
        name: "report_postings",
        full_name: "mcp__app__report_postings",
        module: null,
        description: null,
        input_schema: null,
        described_at: null,
        on: true,
      },
    ],
    turn_budgeted: false,
    ends: ["The model gives its final answer."],
    claude_code: { permission_mode: "dontAsk", setting_sources: [], tools: [] },
    environment:
      "The credential in ANTHROPIC_API_KEY (an API key). Values are never shown.",
    gate: "A PreToolUse hook denies any tool outside allowed_tools.",
    skills: "None: setting_sources is empty and the Skill tool is denied.",
    guards: "Whatever a saved prompt says, the code keeps its guards.",
    version: null,
    code_changed: [],
    code_prompt_then: null,
    versions: [],
    runs: [
      {
        id: "00000000-0000-4000-8000-000000000001",
        status: "done",
        model: "claude-sonnet-5-5",
        version: null,
        turns: 12,
        input_tokens: 900,
        output_tokens: 40,
        created_at: "2026-11-04T09:00:00Z",
        started_at: "2026-11-04T09:00:05Z",
        finished_at: "2026-11-04T09:10:00Z",
        user_name: "E2E Admin",
        user_email: "e2e-admin@example.org",
        mine: true,
      },
      {
        id: "00000000-0000-4000-8000-000000000002",
        status: "failed",
        model: null,
        version: 1,
        turns: null,
        input_tokens: null,
        output_tokens: null,
        created_at: "2026-11-04T08:00:00Z",
        started_at: null,
        finished_at: null,
        user_name: "",
        user_email: "e2e-friend-with-a-very-long-address-name@example.org",
        mine: false,
      },
    ],
    choices: {
      models: ["claude-opus-5-5", "claude-sonnet-5-5"],
      efforts: ["low", "medium", "high", "xhigh", "max"],
      credential_kinds: ["claude_subscription", "anthropic_api_key"],
      turns_max: 10000,
      wall_time_min_s: 10,
      wall_time_max_s: 86400,
      wall_time_where:
        "It runs as its own job on the agents queue, which lets a run take this long.",
      prompt_max: 100000,
      description_max: 300,
      message_max: 4000,
      note_max: 500,
    },
    ...overrides,
  };
}

const SUMMARY: AgentSummary = {
  name: "browser_discovery",
  description: CODE.description,
  module: "app.browser.finder",
  model: "claude-sonnet-5-5",
  test_only: false,
  internal: true,
  private_io: true,
  version: null,
  overridden: false,
  code_changed: [],
  last_run_at: null,
  category: "finding",
};

// The api's groups (catalog.CATEGORIES), in its order, here without their workflows.
const GROUPS: Omit<AgentCategory, "flow">[] = [
  {
    key: "finding",
    title: "Finding jobs",
    sentence:
      "These agents search job websites and company careers pages for postings.",
  },
  {
    key: "applying",
    title: "Applying",
    sentence:
      "These agents fill in application forms, answer their open questions and check the answers before anything is submitted.",
  },
  {
    key: "documents",
    title: "Documents",
    sentence:
      "These agents write and change the CVs, cover letters and proposals.",
  },
  {
    key: "profile",
    title: "Profile and import",
    sentence:
      "These agents read the documents a person uploads, transcribe their scanned pages and propose what goes into their profile.",
  },
  {
    key: "jobs",
    title: "Jobs and chat",
    sentence:
      "These agents assess a posting against the profile and answer questions about a job in its chat.",
  },
  {
    key: "test",
    title: "Test",
    sentence:
      "These agents exist for the dev run panel and the tests, and are never listed in production.",
  },
];

const CATEGORIES: AgentCategory[] = GROUPS.map((group) => ({
  ...group,
  flow: null,
}));

const agent = (
  name: string,
  category: string,
  extra: Partial<AgentSummary> = {},
): AgentSummary => ({
  ...SUMMARY,
  name,
  category,
  description: `The ${name} agent.`,
  internal: false,
  private_io: false,
  ...extra,
});

const noDashes = (text: string | null) => {
  expect(text ?? "").not.toMatch(/[–—]| - /);
};

/** A fake /api/admin/agents holding one page, recording the saves it gets. */
function fakeAgents(
  page: AgentPage = makePage(),
  save?: (body: Record<string, unknown>) => Response | undefined,
  summary: AgentSummary | AgentSummary[] = SUMMARY,
  categories: AgentCategory[] | null = CATEGORIES,
) {
  const state = { page, saves: [] as Array<Record<string, unknown>>, reads: 0 };
  const items = Array.isArray(summary) ? summary : [summary];
  server.use(
    http.get("*/api/admin/agents", () =>
      HttpResponse.json(categories ? { items, categories } : { items }),
    ),
    http.get("*/api/admin/agents/:name", ({ params }) => {
      state.reads += 1;
      if (params.name !== state.page.name)
        return HttpResponse.json(
          { detail: "No agent has this name." },
          { status: 404 },
        );
      return HttpResponse.json(state.page);
    }),
    http.post("*/api/admin/agents/:name/versions", async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      state.saves.push(body);
      const override = save?.(body);
      if (override) return override;
      const changes = body.changes as Partial<Editable>;
      const version = (state.page.version ?? 0) + 1;
      state.page = {
        ...state.page,
        saved: changes,
        effective: { ...CODE, ...changes },
        code_changed: [],
        code_prompt_then: null,
        version,
        versions: [
          {
            version,
            fields: Object.keys(changes).sort(),
            saved_by: "E2E Admin",
            saved_at: new Date().toISOString(),
            note: (body.note as string | null) ?? null,
          },
          ...state.page.versions,
        ],
      };
      return HttpResponse.json(state.page, { status: 201 });
    }),
  );
  return state;
}

/** What the Identity section says the runs use: a version, or the code's definition. */
function runsUse() {
  const identity = screen.getByRole("region", { name: "Identity" });
  return within(identity).getByText("Runs use").nextElementSibling;
}

describe("the list", () => {
  it("shows every agent with its module, model, version and last run", async () => {
    fakeSession(admin);
    fakeAgents();
    const { container } = renderApp("/admin/agents");
    const table = await screen.findByRole("table", {
      name: "Finding jobs, by name",
    });
    const row = within(table).getByRole("row", { name: /browser_discovery/ });
    expect(
      within(row).getByRole("link", { name: "browser_discovery" }),
    ).toHaveAttribute("href", "/admin/agents/browser_discovery");
    expect(within(row).getByText("app.browser.finder")).toBeInTheDocument();
    expect(within(row).getByText("Internal")).toBeInTheDocument();
    expect(within(row).getByText("Private")).toBeInTheDocument();
    expect(within(row).getByText("The code's")).toBeInTheDocument();
    expect(within(row).getByText("Never")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Agents" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(
      screen.getByRole("heading", { name: "Admin", level: 1 }),
    ).toBeInTheDocument();
    expect(document.title).toBe("Agents · jobapp");
    // Phone width: each cell carries its label for the stacked layout.
    expect(container.querySelector('[data-label="Model"]')).not.toBeNull();
    noDashes(container.textContent);
  });

  it("says when the code changed a field since the version that runs was saved", async () => {
    fakeSession(admin);
    fakeAgents(makePage(), undefined, {
      ...SUMMARY,
      version: 2,
      overridden: true,
      code_changed: ["system_prompt"],
    });
    renderApp("/admin/agents");
    const table = await screen.findByRole("table", {
      name: "Finding jobs, by name",
    });
    const row = within(table).getByRole("row", { name: /browser_discovery/ });
    expect(within(row).getByText("Version 2")).toBeInTheDocument();
    const notice = within(row).getByText(
      "The code's prompt changed since it was saved",
    );
    // It wraps on a phone's card line instead of widening the page.
    expect(notice.className).toContain("whitespace-normal");
    noDashes(table.textContent);
  });

  it("groups the agents by category: one section each, in order, with its count and sentence", async () => {
    fakeSession(admin);
    fakeAgents(makePage(), undefined, [
      agent("apply_answer", "applying"),
      agent("apply_form", "applying"),
      SUMMARY,
      agent("documents_letter", "documents"),
      agent("documents_cv", "documents", {
        version: 3,
        overridden: true,
        code_changed: ["model"],
      }),
      agent("echo", "test", { test_only: true }),
      agent("triage", "jobs"),
    ]);
    const { container } = renderApp("/admin/agents");
    await screen.findByRole("table", { name: "Finding jobs, by name" });
    // In the api's order; Profile and import has no agent here, so it is not shown.
    expect(
      screen
        .getAllByRole("heading", { level: 2 })
        .map((heading) => heading.textContent),
    ).toEqual([
      "Finding jobs (1)",
      "Applying (2)",
      "Documents (2)",
      "Jobs and chat (1)",
      "Test (1)",
    ]);
    const applying = screen.getByRole("region", { name: "Applying (2)" });
    expect(
      within(applying).getByText(/^These agents fill in application forms/),
    ).toBeVisible();
    const table = within(applying).getByRole("table", {
      name: "Applying, by name",
    });
    // Same columns as before the grouping, the agent's name its row header.
    expect(
      within(table)
        .getAllByRole("columnheader")
        .map((head) => head.textContent),
    ).toEqual(["Agent", "Module", "Model", "Version", "Last run"]);
    expect(
      within(table)
        .getAllByRole("rowheader")
        .map((head) => within(head).getByRole("link").textContent),
    ).toEqual(["apply_answer", "apply_form"]);
    const documents = screen.getByRole("region", { name: "Documents (2)" });
    expect(
      within(documents).getByRole("link", { name: "documents_letter" }),
    ).toBeInTheDocument();
    // A saved version and a code change notice keep their badges in the section.
    const cv = within(documents).getByRole("row", { name: /documents_cv/ });
    expect(within(cv).getByText("Version 3")).toBeInTheDocument();
    expect(
      within(cv).getByText("The code changed since it was saved"),
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole("region", { name: "Test (1)" })).getByText(
        "Test only",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: /^Profile and import/ }),
    ).toBeNull();
    noDashes(container.textContent);
  });

  it("shows no Test section when the api lists no test agent, as in production", async () => {
    fakeSession(admin);
    fakeAgents(makePage(), undefined, [SUMMARY, agent("triage", "jobs")]);
    renderApp("/admin/agents");
    await screen.findByRole("table", { name: "Finding jobs, by name" });
    expect(
      screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent),
    ).toEqual(["Finding jobs (1)", "Jobs and chat (1)"]);
    expect(screen.queryByRole("heading", { name: /^Test/ })).toBeNull();
    expect(screen.queryByText("Test only")).toBeNull();
  });

  it("an agent of a category the answer does not list comes last, under Other", async () => {
    fakeSession(admin);
    // An api older than the page: no categories, and no category on the agents.
    const old = { ...SUMMARY } as Partial<AgentSummary>;
    delete old.category;
    fakeAgents(
      makePage(),
      undefined,
      [old as AgentSummary, agent("triage", "jobs")],
      null,
    );
    renderApp("/admin/agents");
    const table = await screen.findByRole("table", { name: "Other, by name" });
    expect(
      within(table).getByRole("link", { name: "browser_discovery" }),
    ).toBeInTheDocument();
    expect(
      within(table).getByRole("link", { name: "triage" }),
    ).toBeInTheDocument();
    expect(
      screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent),
    ).toEqual(["Other (2)"]);
  });

  it("gives every section's table the same fixed columns, one line for Model, Version and Last run", async () => {
    fakeSession(admin);
    fakeAgents(makePage(), undefined, [
      agent("documents_letter", "documents"),
      agent("triage", "jobs"),
    ]);
    renderApp("/admin/agents");
    const widths: string[][] = [];
    for (const name of ["Documents, by name", "Jobs and chat, by name"]) {
      const table = await screen.findByRole("table", { name });
      expect(table).toHaveClass("lg:table-fixed");
      const heads = within(table).getAllByRole("columnheader");
      widths.push(heads.map((head) => head.className));
      for (const label of ["Model", "Version", "Last run"]) {
        expect(
          within(table).getByRole("columnheader", { name: label }),
        ).toHaveClass("lg:whitespace-nowrap");
      }
    }
    expect(widths[0]).toEqual(widths[1]);
  });

  it("says so when no agent is registered", async () => {
    fakeSession(admin);
    fakeAgents(makePage(), undefined, []);
    renderApp("/admin/agents");
    expect(
      await screen.findByText("No agent is registered"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("a member gets the not found page and the agents are never asked for", async () => {
    fakeSession(member);
    const state = fakeAgents();
    renderApp("/admin/agents/browser_discovery");
    expect(
      await screen.findByRole("heading", { name: "Page not found" }),
    ).toBeInTheDocument();
    expect(state.reads).toBe(0);
  });
});

describe("an agent's page", () => {
  it("shows every section, the guards and the options, and links only the admin's own runs", async () => {
    fakeSession(admin);
    fakeAgents();
    const { container } = renderApp("/admin/agents/browser_discovery");
    expect(
      await screen.findByRole("heading", { name: "Identity", level: 2 }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "browser_discovery", level: 1 }),
    ).toBeInTheDocument();
    expect(crumb("Agents")).toHaveAttribute("href", "/admin/agents");
    expect(crumb("Admin")).toHaveAttribute("href", "/admin/users");
    for (const title of [
      "Identity",
      "Parameters it takes",
      "System prompt",
      "First message",
      "Tools",
      "Model and limits",
      "How Claude Code is run for it",
      "Recent runs",
      "History of versions",
    ])
      expect(
        screen.getByRole("heading", { name: title, level: 2 }),
      ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Whatever a saved prompt says, the code keeps its guards.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Claude Agent SDK options")).toHaveTextContent(
      '"permission_mode": "dontAsk"',
    );
    expect(screen.getByText(/Not described yet/)).toBeInTheDocument();
    expect(runsUse()).toHaveTextContent("The code's definition");
    const runs = screen.getByRole("table", {
      name: "Recent runs, newest first",
    });
    expect(within(runs).getAllByRole("link")).toHaveLength(1);
    expect(within(runs).getByRole("link")).toHaveAttribute(
      "href",
      "/runs/00000000-0000-4000-8000-000000000001",
    );
    expect(within(runs).getByText("Version 1")).toBeInTheDocument();
    expect(
      screen.getByText(/A private agent's runs keep their numbers only/),
    ).toBeInTheDocument();
    noDashes(container.textContent);
    // Light and dark come from the theme tokens: no fixed colour outside the prompt's diff.
    expect(container.innerHTML).not.toMatch(
      /(bg|text|border)-(white|black|gray|slate|zinc)\b|#[0-9a-f]{6}/i,
    );
  });

  it("shows the default message only for an agent whose first message reads it", async () => {
    fakeSession(admin);
    fakeAgents();
    const { unmount } = renderApp("/admin/agents/browser_discovery");
    expect(
      await screen.findByText(
        "For a website search, a JSON object search_scope.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Default message")).toBeNull();
    unmount();

    fakeAgents(
      makePage({
        uses_default_message: true,
        first_message: "The input's message, else the default message below.",
      }),
    );
    renderApp("/admin/agents/browser_discovery");
    expect(await screen.findByLabelText("Default message")).toHaveValue("");
    expect(
      screen.getByText(
        "Used when a run's input carries no message of its own.",
      ),
    ).toBeInTheDocument();
  });

  it("says what the code changed since the version that runs, with the code's own diff, and reverts", async () => {
    fakeSession(admin);
    const saved = { system_prompt: "Saved prompt.", max_turns: 30 };
    const state = fakeAgents(
      makePage({
        saved,
        effective: { ...CODE, ...saved },
        version: 2,
        code_changed: ["system_prompt", "max_turns", "tools"],
        code_prompt_then: "You search one job website.\nReport the postings.",
      }),
    );
    const user = userEvent.setup();
    const { container } = renderApp("/admin/agents/browser_discovery");
    const notice = (
      await screen.findByText(
        "The code's prompt changed since you saved this version.",
      )
    ).closest('[role="alert"]');
    expect(notice).not.toBeNull();
    const scope = within(notice as HTMLElement);
    const diff = scope.getByRole("list", {
      name: "What the code changed in its prompt",
    });
    expect(
      within(diff).getByText("Report what you verified.").closest("li"),
    ).toHaveAttribute("data-kind", "added");
    expect(
      within(diff).getByText("Report the postings.").closest("li"),
    ).toHaveAttribute("data-kind", "removed");
    expect(
      scope.getByText(
        "The code's max turns changed since you saved this version. The code's value now: 10000.",
      ),
    ).toBeInTheDocument();
    expect(
      scope.getByText(/The code's tools changed since you saved this version/),
    ).toHaveTextContent("browser_snapshot, report_postings.");
    expect(scope.getByText(/Runs still use version 2/)).toBeInTheDocument();
    // The run's prompt is still the saved one until a new version is saved.
    expect(screen.getByRole("textbox", { name: "System prompt" })).toHaveValue(
      "Saved prompt.",
    );
    noDashes(container.textContent);

    await user.click(
      scope.getByRole("button", { name: "Revert to the code's version" }),
    );
    const dialog = await screen.findByRole("alertdialog");
    expect(state.saves).toEqual([]);
    await user.click(within(dialog).getByRole("button", { name: "Revert" }));
    await waitFor(() => {
      expect(state.saves).toEqual([
        { changes: {}, note: REVERT_NOTE, base_version: 2 },
      ]);
    });
    await waitFor(() => {
      expect(
        screen.queryByText(
          "The code's prompt changed since you saved this version.",
        ),
      ).toBeNull();
    });
  });

  it("starts another agent's page from its own values, not from the last page's draft", async () => {
    fakeSession(admin);
    const triage = makePage({
      name: "triage",
      code: { ...CODE, description: "Assesses one posting." },
      effective: { ...CODE, description: "Assesses one posting." },
    });
    const pages: Record<string, AgentPage> = {
      browser_discovery: makePage(),
      triage,
    };
    server.use(
      http.get("*/api/admin/agents/:name", ({ params }) =>
        HttpResponse.json(pages[String(params.name)]),
      ),
    );
    const user = userEvent.setup();
    const { router } = renderApp("/admin/agents/triage");
    expect(await screen.findByLabelText("Description")).toHaveValue(
      "Assesses one posting.",
    );
    await act(() => router.navigate("/admin/agents/browser_discovery"));
    const description = await screen.findByDisplayValue(CODE.description);
    await user.clear(description);
    await user.type(description, "A draft of the other agent.");
    // Straight to the cached page of triage, as a jump in the history does.
    await act(() => router.navigate("/admin/agents/triage"));
    expect(
      await screen.findByRole("heading", { name: "triage", level: 1 }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Description")).toHaveValue(
      "Assesses one posting.",
    );
    expect(
      screen.getByRole("button", { name: "Save as a new version" }),
    ).toBeDisabled();
  });

  it("an unknown agent says so", async () => {
    fakeSession(admin);
    fakeAgents();
    renderApp("/admin/agents/nobody");
    expect(
      await screen.findByText("No agent has this name"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "See all agents" }),
    ).toHaveAttribute("href", "/admin/agents");
  });
});

describe("editing", () => {
  it("refuses an empty prompt and a wall time above the code's, then saves the changed fields only", async () => {
    fakeSession(admin);
    const state = fakeAgents();
    const user = userEvent.setup();
    renderApp("/admin/agents/browser_discovery");
    const prompt = await screen.findByRole("textbox", {
      name: "System prompt",
    });
    const save = screen.getByRole("button", { name: "Save as a new version" });
    expect(save).toBeDisabled(); // nothing to save yet
    await user.clear(prompt);
    expect(screen.getByText("The prompt cannot be empty.")).toBeInTheDocument();
    expect(save).toBeDisabled();
    await user.type(
      prompt,
      "You search one job website.{Enter}Stay on the site.",
    );
    const diff = screen.getByRole("list", {
      name: "Changes from the code's prompt",
    });
    expect(
      within(diff).getByText("Stay on the site.").closest("li"),
    ).toHaveAttribute("data-kind", "added");
    expect(
      within(diff).getByText("Report what you verified.").closest("li"),
    ).toHaveAttribute("data-kind", "removed");

    const wall = screen.getByLabelText("Wall time, in seconds");
    await user.clear(wall);
    await user.type(wall, "90000");
    expect(
      screen.getByText(
        "From 10 to 86,400 seconds: never more than the job this agent runs in allows.",
      ),
    ).toBeInTheDocument();
    expect(save).toBeDisabled();
    await user.clear(wall);
    await user.type(wall, "600");
    expect(screen.getByText("The code's: 86400")).toBeInTheDocument();

    await user.click(screen.getByRole("checkbox", { name: /report_postings/ }));
    await user.type(
      screen.getByLabelText("Note, optional"),
      "Stay on the site",
    );
    expect(save).toBeEnabled();
    await user.click(save);
    await waitFor(() => {
      expect(state.saves).toHaveLength(1);
    });
    expect(state.saves[0]).toEqual({
      changes: {
        system_prompt: "You search one job website.\nStay on the site.",
        wall_time_s: 600,
        tools: ["browser_snapshot"],
      },
      note: "Stay on the site",
      base_version: null,
    });
    const history = await screen.findByRole("list", {
      name: "Versions, newest first",
    });
    expect(within(history).getByText(/Version 1/)).toHaveTextContent(
      "Version 1 (runs use it)",
    );
    expect(
      within(history).getByText(/Changes system_prompt, tools, wall_time_s/),
    ).toBeInTheDocument();
    expect(
      await screen.findByText(
        "Version 1 saved. The next run of browser_discovery uses it.",
      ),
    ).toBeInTheDocument();
    expect(runsUse()).toHaveTextContent("Version 1");
    expect(
      screen.getByRole("button", { name: "Save as a new version" }),
    ).toBeDisabled();
  });

  it("keeps the typing when the page is read again, and says when another version came in", async () => {
    fakeSession(admin);
    const state = fakeAgents();
    const user = userEvent.setup();
    const { client } = renderApp("/admin/agents/browser_discovery");
    const description = await screen.findByLabelText("Description");
    await user.clear(description);
    await user.type(description, "Searches a website.");
    state.page = {
      ...state.page,
      version: 4,
      saved: { effort: "high" },
      effective: { ...CODE, effort: "high" },
    };
    await client.invalidateQueries({
      queryKey: ["admin", "agents", "browser_discovery"],
    });
    expect(
      await screen.findByText(/Version 4 was saved since you opened this page/),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Description")).toHaveValue(
      "Searches a website.",
    );
    await user.click(
      screen.getByRole("button", { name: "Save as a new version" }),
    );
    await waitFor(() => {
      expect(state.saves).toHaveLength(1);
    });
    expect(state.saves[0]?.base_version).toBeNull(); // the version the draft started from
  });

  it("shows the api's refusal of a stale save", async () => {
    fakeSession(admin);
    const stale =
      "Another version was saved since this page was read. Reload it, then save again.";
    fakeAgents(makePage(), () =>
      HttpResponse.json({ detail: stale }, { status: 409 }),
    );
    const user = userEvent.setup();
    renderApp("/admin/agents/browser_discovery");
    await user.selectOptions(await screen.findByLabelText("Effort"), "high");
    await user.click(
      screen.getByRole("button", { name: "Save as a new version" }),
    );
    expect(await screen.findByText(stale)).toBeInTheDocument();
    expect(screen.getByLabelText("Effort")).toHaveValue("high");
  });

  it("reverts to the code's version after a confirmation, as a new version", async () => {
    fakeSession(admin);
    const saved = { system_prompt: "Saved prompt." };
    const state = fakeAgents(
      makePage({
        saved,
        effective: { ...CODE, ...saved },
        version: 2,
        versions: [
          {
            version: 2,
            fields: ["system_prompt"],
            saved_by: "E2E Admin",
            saved_at: "2026-11-04T09:00:00Z",
            note: null,
          },
          {
            version: 1,
            fields: [],
            saved_by: null,
            saved_at: "2026-11-03T09:00:00Z",
            note: "First",
          },
        ],
      }),
    );
    const user = userEvent.setup();
    renderApp("/admin/agents/browser_discovery");
    await screen.findByRole("region", { name: "Identity" });
    expect(runsUse()).toHaveTextContent("Version 2");
    expect(screen.getByText(/an account that is gone/)).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "Revert to the code's version" }),
    );
    const dialog = await screen.findByRole("alertdialog");
    expect(state.saves).toEqual([]);
    await user.click(within(dialog).getByRole("button", { name: "Revert" }));
    await waitFor(() => {
      expect(state.saves).toEqual([
        { changes: {}, note: REVERT_NOTE, base_version: 2 },
      ]);
    });
    expect(
      await screen.findByRole("textbox", { name: "System prompt" }),
    ).toHaveValue(CODE.system_prompt);
  });
});

describe("A7: the ceiling of a wall time and the person of each run", () => {
  it("shows the ceiling by the field and saves a wall time raised above the code's", async () => {
    fakeSession(admin);
    const code = { ...CODE, wall_time_s: 600 };
    const state = fakeAgents(makePage({ code, effective: code }));
    const user = userEvent.setup();
    const { container } = renderApp("/admin/agents/browser_discovery");
    const wall = await screen.findByLabelText("Wall time, in seconds");
    expect(wall).toHaveAttribute("max", "86400");
    expect(wall).toHaveAccessibleDescription(
      /At most 86,400 seconds \(24 hours\) for this agent\. It runs as its own job on the agents queue, which lets a run take this long\. Lower or raise it within that\./,
    );
    await user.clear(wall);
    await user.type(wall, "86401");
    expect(
      screen.getByText(/never more than the job this agent runs in allows/),
    ).toBeInTheDocument();
    const save = screen.getByRole("button", { name: "Save as a new version" });
    expect(save).toBeDisabled();
    await user.clear(wall);
    await user.type(wall, "7200");
    expect(screen.getByText("The code's: 600")).toBeInTheDocument();
    expect(
      screen.queryByText(/never more than the job this agent runs in/),
    ).not.toBeInTheDocument();
    await user.click(save);
    await waitFor(() => {
      expect(state.saves).toHaveLength(1);
    });
    expect(state.saves[0]?.changes).toEqual({ wall_time_s: 7200 });
    noDashes(container.textContent);
  });

  it("shows the ceiling of an agent that runs inside another job, and where it runs", async () => {
    fakeSession(admin);
    const page = makePage();
    const where =
      "It runs inside the decision of a Full auto item (apply_decide), whose job ends there.";
    fakeAgents(
      makePage({
        choices: {
          ...page.choices,
          wall_time_max_s: 3600,
          wall_time_where: where,
        },
      }),
    );
    const user = userEvent.setup();
    const { container } = renderApp("/admin/agents/browser_discovery");
    const wall = await screen.findByLabelText("Wall time, in seconds");
    expect(wall).toHaveAttribute("max", "3600");
    expect(wall).toHaveAccessibleDescription(
      /At most 3,600 seconds \(1 hour\) for this agent\. It runs inside the decision of a Full auto item \(apply_decide\), whose job ends there\./,
    );
    await user.clear(wall);
    await user.type(wall, "3601");
    expect(
      screen.getByText(
        /From 10 to 3,600 seconds: never more than the job this agent runs in allows\./,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Save as a new version" }),
    ).toBeDisabled();
    noDashes(container.textContent);
  });

  it("names the person of each run, as the Users page does, and says when a run has not started", async () => {
    fakeSession(admin);
    fakeAgents();
    const { container } = renderApp("/admin/agents/browser_discovery");
    const runs = await screen.findByRole("table", {
      name: "Recent runs, newest first",
    });
    expect(
      within(runs).getByRole("columnheader", { name: "Person" }),
    ).toBeInTheDocument();
    const [, own, theirs] = within(runs).getAllByRole("row");
    if (!own || !theirs) throw new Error("the two runs of the page");
    // The admin's own run: the name, the e-mail below it, and the link to its page.
    expect(within(own).getByText("E2E Admin")).toBeInTheDocument();
    expect(within(own).getByText("e2e-admin@example.org")).toBeInTheDocument();
    expect(within(own).getByRole("link")).toHaveTextContent(/2026/);
    // Another person's queued run of a private agent: no name, so the e-mail stands for it;
    // no link; and not started yet.
    expect(
      within(theirs).getAllByText(
        "e2e-friend-with-a-very-long-address-name@example.org",
      ),
    ).toHaveLength(2);
    expect(within(theirs).queryByRole("link")).not.toBeInTheDocument();
    expect(within(theirs).getByText("Not started")).toBeInTheDocument();
    // At phone width each row is a card and each cell a labelled line.
    const person = container.querySelector('[data-label="Person"]');
    expect(person).not.toBeNull();
    expect(person?.className).toMatch(/max-sm:grid/);
    expect(person?.innerHTML).toMatch(/break-all/);
    noDashes(runs.textContent);
    noDashes(container.textContent);
  });
});

describe("changesOf", () => {
  it("sends the fields that differ from the code, mappings compared by content", () => {
    expect(
      changesOf(CODE, {
        ...CODE,
        model_by_credential: { anthropic_api_key: "claude-sonnet-5-5" },
      }),
    ).toEqual({});
    expect(
      changesOf(CODE, { ...CODE, effort: "high", model_by_credential: {} }),
    ).toEqual({
      effort: "high",
      model_by_credential: {},
    });
  });
});
