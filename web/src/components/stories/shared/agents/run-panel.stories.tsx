import type { Meta, StoryObj } from "@storybook/react-vite";

import { RunPanel } from "@/components/shared/agents/run-panel";
import {
  FINISHED_STEPS,
  makeRun,
  RUN_ID,
  RUNNING_STEPS,
} from "@/design-system/fixtures/runs";
import {
  failRun,
  FAILED_STEPS,
  fakeRuns,
  LONG_RESULT_STEPS,
  WRITING,
} from "@/design-system/fixtures/shared-agents";
import { withApp } from "@/design-system/with-app";

const AT_WORK = {
  status: "running" as const,
  cost_usd: null,
  input_tokens: null,
  output_tokens: null,
  started_at: new Date(Date.now() - 25_000).toISOString(),
  finished_at: null,
  duration_s: null,
};

const meta = {
  title: "Shared/Agents/Run panel",
  component: RunPanel,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "One agent run, live: its status, the live connection, model, tokens, amount and duration, its steps as they happen, and Cancel while it works. Embedded in Agent assessment on a posting and in a profile reading.",
      },
    },
  },
  args: { runId: RUN_ID },
} satisfies Meta<typeof RunPanel>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A posting check that ended: every step, and what it cost. */
export const FinishedRun: Story = {
  name: "Finished run",
  beforeEach: () => {
    fakeRuns({ run: makeRun() });
  },
};

/** The run works: Live, the duration counts, and Cancel stops it. */
export const RunAtWork: Story = {
  name: "Run at work, with Cancel",
  beforeEach: () => {
    fakeRuns({
      run: makeRun(AT_WORK),
      steps: RUNNING_STEPS,
      open: true,
    });
  },
};

/** The answer appears while it is written, as the last step. */
export const AnswerBeingWritten: Story = {
  name: "Answer being written",
  beforeEach: () => {
    fakeRuns({
      run: makeRun(AT_WORK),
      steps: WRITING.steps,
      writing: WRITING.part,
    });
  },
};

/** Cancel was clicked: the button says Stopping until the run ends. */
export const Stopping: Story = {
  name: "Stopping after Cancel",
  beforeEach: () => {
    fakeRuns({
      run: makeRun({ ...AT_WORK, cancel_requested: true }),
      steps: RUNNING_STEPS,
      open: true,
    });
  },
};

/** Where the page has a Cancel of its own, the panel shows none. */
export const WithoutItsCancel: Story = {
  name: "Run at work, the page cancels",
  args: { cancellable: false, title: "Reading your CV" },
  beforeEach: () => {
    fakeRuns({
      run: makeRun({ ...AT_WORK, agent: "ingest_profile" }),
      steps: RUNNING_STEPS,
      open: true,
    });
  },
};

/** The run failed: the last step says why, in red. */
export const FailedRun: Story = {
  name: "Failed run",
  beforeEach: () => {
    fakeRuns({
      run: makeRun({
        agent: "apply_form",
        status: "failed",
        error: "max_turns",
        credential_kind: "anthropic_api_key",
      }),
      steps: FAILED_STEPS,
    });
  },
};

/** The person cancelled it. */
export const CancelledRun: Story = {
  name: "Cancelled run",
  beforeEach: () => {
    fakeRuns({
      run: makeRun({ status: "cancelled", cancel_requested: true }),
      steps: [
        ...RUNNING_STEPS,
        {
          seq: 7,
          at: "2026-11-05T08:14:09Z",
          kind: "status",
          data: { status: "cancelled" },
        },
      ],
    });
  },
};

/** The stream cannot be reached: the panel says so and tries again by itself. */
export const Reconnecting: Story = {
  name: "Connection lost, reconnecting",
  beforeEach: () => {
    fakeRuns({ run: makeRun(AT_WORK), steps: RUNNING_STEPS, open: true });
    failRun("stream down");
  },
};

/** A long tool result folds; a long answer wraps. */
export const LongContent: Story = {
  name: "Long result and long answer",
  beforeEach: () => {
    fakeRuns({ run: makeRun(), steps: LONG_RESULT_STEPS });
  },
};

/** The run is read: a placeholder keeps its place. */
export const Loading: Story = {
  name: "Loading",
  beforeEach: () => {
    failRun("loading");
  },
};

/** The run could not be read: say so, with Retry. */
export const LoadFailed: Story = {
  name: "Could not be loaded",
  beforeEach: () => {
    failRun("error");
  },
};

/** The run was deleted, or is not the person's. */
export const Gone: Story = {
  name: "Run no longer exists",
  beforeEach: () => {
    fakeRuns({ run: makeRun(), gone: true });
  },
};

/** On a phone the facts and the steps stack in one column. */
export const OnAPhone: Story = {
  name: "On a phone",
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
  beforeEach: () => {
    fakeRuns({ run: makeRun(), steps: FINISHED_STEPS });
  },
};
