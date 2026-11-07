import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { LiveReply } from "@/components/shared/agents/chat/live-reply";
import {
  FINISHED_STEPS,
  makeRun,
  RUN_ID,
  RUNNING_STEPS,
} from "@/design-system/fixtures/runs";
import { fakeRuns, WRITING } from "@/design-system/fixtures/shared-agents";
import { withApp } from "@/design-system/with-app";

const AT_WORK = makeRun({
  agent: "chat",
  status: "running",
  finished_at: null,
  duration_s: null,
});

const meta = {
  title: "Shared/Agents/Live reply",
  component: LiveReply,
  tags: ["autodocs"],
  decorators: [
    withApp,
    (Story) => (
      <div className="max-w-md">
        <Story />
      </div>
    ),
  ],
  parameters: {
    docs: {
      description: {
        component:
          "The agent's answer while it is written, in the Chat panel of a posting: what the agent does until text arrives, then the text as it grows, then the stored answer in the same bubble.",
      },
    },
  },
  args: { runId: RUN_ID, onEnd: fn() },
} satisfies Meta<typeof LiveReply>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Every agent is busy: the message waits for one. */
export const WaitingForAnAgent: Story = {
  name: "Waiting for a free agent",
  beforeEach: () => {
    fakeRuns({
      run: makeRun({ ...AT_WORK, status: "queued" }),
      steps: [],
      open: true,
    });
  },
};

/** The agent reads the posting before it answers. */
export const ReadingThePosting: Story = {
  name: "Reading the posting",
  beforeEach: () => {
    fakeRuns({ run: AT_WORK, steps: RUNNING_STEPS.slice(0, 4), open: true });
  },
};

/** The text grows in the bubble; the line under it says Writing, and breathes. */
export const Writing: Story = {
  name: "Writing the answer",
  beforeEach: () => {
    fakeRuns({ run: AT_WORK, steps: WRITING.steps, writing: WRITING.part });
  },
};

/** The run ended: the stored answer stays until the conversation is read again. */
export const Answered: Story = {
  name: "Answer complete",
  beforeEach: () => {
    fakeRuns({ run: makeRun({ agent: "chat" }), steps: FINISHED_STEPS });
  },
};
