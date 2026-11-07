import type { Meta, StoryObj } from "@storybook/react-vite";

import { RunActions } from "@/components/shared/ingestion/run-progress/run-actions";
import {
  pausedRun,
  runningRun,
} from "@/design-system/fixtures/camille-ingestion";
import {
  pausedOnFailureRun,
  stoppingRun,
} from "@/design-system/fixtures/shared-ingestion";

const meta = {
  title: "Shared/Ingestion/Run actions",
  component: RunActions,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The one action that leaves each state of a run, and Cancel while it can still stop: under the status in Run progress. Continue carries the tokens it adds, the figure the person agrees to.",
      },
    },
  },
  args: { run: runningRun(), busy: false, onAction: () => undefined },
} satisfies Meta<typeof RunActions>;

export default meta;
type Story = StoryObj<typeof meta>;

/** While it reads: Cancel the run. */
export const WhileItReads: Story = { name: "While it reads" };

/** Cancel already asked: the button stays off until the run ends. */
export const CancelAsked: Story = {
  name: "Cancel already asked",
  args: { run: stoppingRun() },
};

/** At its limit: Continue with the tokens it adds, or Cancel. */
export const AtTheLimit: Story = {
  name: "At its token limit",
  args: { run: pausedRun() },
};

/** Paused by a failure: Resume, or Cancel. */
export const PausedByAFailure: Story = {
  name: "Paused by a failure",
  args: { run: pausedOnFailureRun() },
};

/** A click on its way: every button waits. */
export const Busy: Story = {
  name: "Waiting for the server",
  args: { run: pausedRun(), busy: true },
};

/** On a phone the buttons wrap. */
export const OnAPhone: Story = {
  name: "On a phone",
  args: { run: pausedRun() },
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
};
