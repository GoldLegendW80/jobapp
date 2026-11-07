import type { Meta, StoryObj } from "@storybook/react-vite";

import { RunTimeline } from "@/components/shared/agents/run/run-timeline";
import { FINISHED_STEPS, RUNNING_STEPS } from "@/design-system/fixtures/runs";
import {
  ANSWER_SO_FAR,
  FAILED_STEPS,
  LONG_RESULT_STEPS,
  PRIVATE_STEPS,
} from "@/design-system/fixtures/shared-agents";

const meta = {
  title: "Shared/Agents/Run timeline",
  component: RunTimeline,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The steps of an agent run, oldest first, with the answer being written last: in the run panel of a posting check and on the page of a run.",
      },
    },
  },
  args: {
    steps: FINISHED_STEPS,
    live: null,
    kind: "claude_subscription",
  },
} satisfies Meta<typeof RunTimeline>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A posting check from start to end: what it read, what it answered, what it cost. */
export const FinishedRun: Story = { name: "Finished run" };

/** Nothing arrived yet. */
export const NoStepYet: Story = { name: "No step yet", args: { steps: [] } };

/** The run is at work: the answer grows at the end, in the same place its stored step takes. */
export const AnswerBeingWritten: Story = {
  name: "Answer being written",
  args: {
    steps: RUNNING_STEPS,
    live: { block: 1, after: 6, text: ANSWER_SO_FAR },
  },
};

/** A refused tool, a rate limit, a model error and a tool error, then the run fails on its turn limit. */
export const FailedRun: Story = {
  name: "Failed run with its problems",
  args: { steps: FAILED_STEPS, kind: "anthropic_api_key" },
};

/** A private run stores the size of arguments and results, never their content. */
export const PrivateRun: Story = {
  name: "Private run, sizes only",
  args: { steps: PRIVATE_STEPS },
};

/** A long tool result folds under its length; a long answer wraps. */
export const LongContent: Story = {
  name: "Long result and long answer",
  args: { steps: LONG_RESULT_STEPS },
};

/** On a phone the arguments and results wrap within the column. */
export const OnAPhone: Story = {
  name: "On a phone",
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
};
