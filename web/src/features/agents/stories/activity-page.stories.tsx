import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";

import { AppAt } from "@/design-system/app-at";
import {
  ASSESS_RUN_ID,
  FAILED_RUN_ID,
  PRIVATE_RUN_ID,
  PRIVATE_STEPS,
  RUN_UNDER_WAY_ID,
  SEARCH_RUN_ID,
  TRIAGE_STEP_ID,
  WEBSITE_STEPS,
  WEBSITE_STEP_IDS,
  fakeActivityApi,
} from "@/design-system/fixtures/activity";
import { FINISHED_STEPS, RUNNING_STEPS } from "@/design-system/fixtures/runs";
import { fakeSession, member } from "@/test/auth";

const meta = {
  title: "Pages/Activity",
  parameters: {
    layout: "fullscreen",
    page: true,
    docs: {
      description: {
        component:
          "The person's workflow runs, in the pattern of GitHub Actions: the workflows on the side, the runs of the one chosen, newest first, with how each was triggered, what it is about, its status, when and how long. A run's page shows its summary, the workflow's graph with each step's status and duration, its steps, and the log of the agent step chosen, live while it runs. Routes /activity, /activity/:workflow and /activity/runs/:id; /runs and /runs/:id lead there. Activity is in the main navigation.",
      },
    },
  },
  render: () => <AppAt path="/activity" />,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** As a job seeker, I want to see every run of every workflow, how each started and how it went, so that I know what the app did for me. */
export const AllWorkflows: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeActivityApi();
  },
};

/** As a job seeker, I want the runs of one workflow only, so that I find the application I am looking for. */
export const OneWorkflow: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeActivityApi();
  },
  render: () => <AppAt path="/activity/application" />,
};

/** As a new user, I want the empty activity to say what will appear here, so that an empty page does not look broken. */
export const NoActivityYet: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeActivityApi({ runs: [] });
  },
};

/** As a job seeker, I want to see where a run is while it works: what is done, what is at work and what comes next, so that I am never waiting on something I cannot see. */
export const ARunUnderWay: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeActivityApi({
      step: {
        changes: { input_tokens: null, output_tokens: null, cost_usd: null },
        steps: RUNNING_STEPS,
        open: true,
      },
    });
  },
  render: () => <AppAt path={`/activity/runs/${RUN_UNDER_WAY_ID}`} />,
};

/** As a job seeker, I want a failed run to show which step failed and why, so that I know what to do next. */
export const AFailedRun: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeActivityApi({
      step: {
        changes: { error: "max_turns", credential_kind: "anthropic_api_key" },
        steps: [
          ...RUNNING_STEPS,
          {
            seq: 7,
            at: "2026-11-05T08:18:51Z",
            kind: "status",
            data: { status: "failed", error: "max_turns" },
          },
        ],
      },
    });
  },
  render: () => <AppAt path={`/activity/runs/${FAILED_RUN_ID}`} />,
};

/** As a job seeker, I want to open a step's log, unfold a tool call and filter the lines, so that I see exactly what the agent read and did. */
export const AStepsLogOpen: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeActivityApi({ step: { steps: FINISHED_STEPS } });
  },
  render: () => (
    <AppAt path={`/activity/runs/${ASSESS_RUN_ID}?step=${TRIAGE_STEP_ID}`} />
  ),
  play: async ({ canvasElement }) => {
    const page = within(canvasElement);
    await userEvent.click(
      await page.findByRole("button", { name: /Tool call: get_job/ }),
    );
    await userEvent.type(
      await page.findByRole("searchbox", { name: "Filter the log" }),
      "Looker",
    );
  },
};

/** As a job seeker, I want a private run's log to name each step without its content, so that my documents never show in a log. */
export const APrivateRun: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeActivityApi({ step: { steps: PRIVATE_STEPS } });
  },
  render: () => <AppAt path={`/activity/runs/${PRIVATE_RUN_ID}`} />,
};

/** As a job seeker, I want a search on two websites side by side to show both, and which one's log I read, so that I can tell them apart. */
export const TwoWebsitesSideBySide: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeActivityApi({ step: { steps: WEBSITE_STEPS } });
  },
  render: () => (
    <AppAt
      path={`/activity/runs/${SEARCH_RUN_ID}?step=${WEBSITE_STEP_IDS[1]}`}
    />
  ),
};

/** As a job seeker on my phone, I want the activity readable at my screen's width, so that I can follow a run away from my desk. */
export const OnAPhone: Story = {
  ...AllWorkflows,
  globals: { viewport: { value: "mobile2", isRotated: false } },
};

/** As a job seeker on my phone, I want a run under way readable too, its graph panned in its frame. */
export const ARunOnAPhone: Story = {
  ...ARunUnderWay,
  globals: { viewport: { value: "mobile2", isRotated: false } },
};

/** As a job seeker who reads at night, I want the run page in the dark theme. */
export const ARunInTheDark: Story = {
  ...ARunUnderWay,
  globals: { theme: "Dark" },
};
