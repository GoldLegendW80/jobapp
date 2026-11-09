import type {
  AgentCategory,
  AgentPage,
  AgentSummary,
  Editable,
} from "@/features/admin/api/agents-api";

const HOUR = 3_600_000;
const DAY = 24 * HOUR;
const ago = (ms: number) => new Date(Date.now() - ms).toISOString();

/** The groups of the list, in the api's order (catalog.CATEGORIES). */
export const AGENT_CATEGORIES: AgentCategory[] = [
  {
    key: "finding",
    title: "Finding jobs",
    sentence: "These agents search job websites for postings.",
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

const summary = (
  name: string,
  category: string,
  description: string,
  changes: Partial<AgentSummary> = {},
): AgentSummary => ({
  name,
  description,
  module: `app.${category}.${name}`,
  model: "claude-sonnet-5-5",
  test_only: false,
  internal: false,
  private_io: false,
  version: null,
  overridden: false,
  code_changed: [],
  last_run_at: ago(3 * HOUR),
  category,
  ...changes,
});

/** Every agent of the app as the list shows it: badges, saved versions, a code change. */
export const AGENTS: AgentSummary[] = [
  summary(
    "browser_discovery",
    "finding",
    "Searches one job website with the browser, as a person would.",
    {
      module: "app.browser.finder",
      internal: true,
      private_io: true,
      version: 2,
      overridden: true,
      code_changed: ["system_prompt"],
      last_run_at: ago(40 * 60_000),
    },
  ),
  summary(
    "careers_page_reader",
    "finding",
    "Reads a company careers page and lists the postings it links to.",
    { internal: true, last_run_at: ago(DAY) },
  ),
  summary(
    "apply_answer",
    "applying",
    "Answers one open question of an application form from the profile.",
    { model: "claude-opus-5-5", private_io: true },
  ),
  summary(
    "apply_form",
    "applying",
    "Fills in an application form in the browser and stops before Submit.",
    { model: "claude-opus-5-5", private_io: true, last_run_at: ago(2 * DAY) },
  ),
  summary(
    "documents_cv",
    "documents",
    "Writes the CV for one posting from the base CV and the profile.",
    {
      version: 3,
      overridden: true,
      code_changed: ["model"],
      last_run_at: ago(5 * HOUR),
    },
  ),
  summary(
    "documents_letter",
    "documents",
    "Writes the cover letter for one posting in the tone the person chose.",
  ),
  summary(
    "profile_import",
    "profile",
    "Reads an uploaded CV and proposes the profile fields it finds.",
    { private_io: true, last_run_at: null },
  ),
  summary(
    "job_assessment",
    "jobs",
    "Assesses a posting against the profile: strengths, gaps and questions.",
  ),
  summary(
    "job_chat",
    "jobs",
    "Answers the person's questions about one job in its chat.",
    { last_run_at: ago(15 * 60_000) },
  ),
  summary("echo", "test", "Repeats the message it is given.", {
    module: "app.agents.test_agents",
    test_only: true,
    last_run_at: null,
  }),
];

/** GET /api/admin/agents. */
export const agentList = (items: AgentSummary[] = AGENTS) => ({
  items,
  categories: AGENT_CATEGORIES,
});

const PROMPT_THEN = [
  "You search one job website for postings that fit the person.",
  "Open the search page, type the roles and places of the search scope.",
  "Report what you verified.",
].join("\n");

const CODE: Editable = {
  description: "Searches one job website with the browser, as a person would.",
  system_prompt: [
    "You search one job website for postings that fit the person.",
    "Open the search page, type the roles and places of the search scope.",
    "Never sign in, never accept cookies beyond what the page needs.",
    "Report each posting once, with its address.",
  ].join("\n"),
  default_message: "",
  model: "claude-sonnet-5-5",
  model_by_credential: { anthropic_api_key: "claude-sonnet-5-5" },
  effort: "low",
  max_turns: 200,
  wall_time_s: 1800,
  tools: ["browser_snapshot", "browser_click", "report_postings"],
};

const SAVED: Partial<Editable> = {
  system_prompt: [
    "You search one job website for postings that fit the person.",
    "Open the search page, type the roles and places of the search scope.",
    "Stay on the website you were given.",
    "Report what you verified.",
  ].join("\n"),
  max_turns: 120,
};

/** One agent's page as it runs on the code's definition: nothing saved yet. */
export const AGENT_PAGE: AgentPage = {
  name: "browser_discovery",
  module: "app.browser.finder",
  starter: "A job search sweep, one run per website",
  flags: [
    "Private: its inputs and outputs are never stored, only their sizes and counts.",
    "Internal: started by the app itself, never by a person.",
  ],
  test_only: false,
  internal: true,
  private_io: true,
  code: CODE,
  saved: {},
  effective: CODE,
  input_fields: [
    {
      name: "search_scope",
      type: "object",
      required: true,
      default: null,
      limits: "roles and places, at most 20 of each",
      description: "What to search for, as the person's search settings say.",
    },
    {
      name: "website",
      type: "string",
      required: true,
      default: null,
      limits: "at most 2,000 characters",
      description: "The address of the job website to search.",
    },
  ],
  input_schema: {
    type: "object",
    required: ["search_scope", "website"],
    properties: {
      search_scope: { type: "object" },
      website: { type: "string", maxLength: 2000 },
    },
  },
  first_message:
    "A JSON object with the search scope and the website, built by the sweep.",
  uses_default_message: false,
  tools: [
    {
      name: "browser_snapshot",
      full_name: "mcp__app__browser_snapshot",
      module: "app.browser.finder_tools",
      description: "Read the page as an accessibility snapshot.",
      input_schema: { type: "object", properties: {} },
      described_at: ago(DAY),
      on: true,
    },
    {
      name: "browser_click",
      full_name: "mcp__app__browser_click",
      module: "app.browser.finder_tools",
      description: "Click one element of the page, by its reference.",
      input_schema: {
        type: "object",
        properties: { ref: { type: "string" } },
      },
      described_at: ago(DAY),
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
  turn_budgeted: true,
  ends: [
    "The model gives its final answer.",
    "The run reaches its max turns or its wall time.",
    "The person cancels the sweep.",
  ],
  claude_code: {
    permission_mode: "dontAsk",
    setting_sources: [],
    allowed_tools: CODE.tools.map((tool) => `mcp__app__${tool}`),
  },
  environment:
    "The credential in ANTHROPIC_API_KEY (an API key). Values are never shown.",
  gate: "A PreToolUse hook denies any tool outside allowed_tools.",
  skills: "None: setting_sources is empty and the Skill tool is denied.",
  guards:
    "Whatever a saved prompt says, the code keeps its guards: the tool gate, the private inputs and the limits below.",
  version: null,
  code_changed: [],
  code_prompt_then: null,
  versions: [],
  runs: [
    {
      id: "00000000-0000-4000-8000-000000000401",
      status: "done",
      model: "claude-sonnet-5-5",
      version: null,
      turns: 34,
      input_tokens: 48210,
      output_tokens: 1290,
      created_at: ago(40 * 60_000),
      started_at: ago(39 * 60_000),
      finished_at: ago(31 * 60_000),
      user_name: "Harald Example",
      user_email: "harald@example.org",
      mine: true,
    },
    {
      id: "00000000-0000-4000-8000-000000000402",
      status: "failed",
      model: "claude-sonnet-5-5",
      version: null,
      turns: 200,
      input_tokens: 190400,
      output_tokens: 5120,
      created_at: ago(DAY),
      started_at: ago(DAY - 60_000),
      finished_at: ago(DAY - 30 * 60_000),
      user_name: "Tomás Okafor",
      user_email: "tomas.okafor@example.org",
      mine: false,
    },
    {
      id: "00000000-0000-4000-8000-000000000403",
      status: "queued",
      model: null,
      version: null,
      turns: null,
      input_tokens: null,
      output_tokens: null,
      created_at: ago(60_000),
      started_at: null,
      finished_at: null,
      user_name: "",
      user_email: "priya.natarajan@example.org",
      mine: false,
    },
  ],
  choices: {
    models: ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"],
    efforts: ["low", "medium", "high", "xhigh", "max"],
    credential_kinds: ["claude_subscription", "anthropic_api_key"],
    turns_max: 10000,
    wall_time_min_s: 10,
    wall_time_max_s: 3600,
    wall_time_where:
      "It runs inside the sweep's job, whose bound is one hour per website.",
    prompt_max: 100000,
    description_max: 300,
    message_max: 4000,
    note_max: 500,
  },
};

/** The same agent with version 2 saved, and the code's prompt and turns changed since. */
export const AGENT_PAGE_CHANGED: AgentPage = {
  ...AGENT_PAGE,
  saved: SAVED,
  effective: { ...CODE, ...SAVED },
  version: 2,
  code_changed: ["system_prompt", "max_turns"],
  code_prompt_then: PROMPT_THEN,
  versions: [
    {
      version: 2,
      fields: ["max_turns", "system_prompt"],
      saved_by: "Harald Example",
      saved_at: ago(6 * DAY),
      note: "Keep it on the website it was given.",
    },
    {
      version: 1,
      fields: ["system_prompt"],
      saved_by: null,
      saved_at: ago(20 * DAY),
      note: null,
    },
  ],
  runs: AGENT_PAGE.runs.map((run) => ({ ...run, version: 2 })),
};
