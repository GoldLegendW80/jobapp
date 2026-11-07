import type { Meta, StoryObj } from "@storybook/react-vite";

import { RunStatus } from "@/components/shared/ingestion/run-progress/run-status";
import {
  doneRun,
  pausedRun,
  runningRun,
} from "@/design-system/fixtures/camille-ingestion";
import {
  cancelledRun,
  closingRun,
  failedRun,
  partialRun,
  pausedOnFailureRun,
  stoppingRun,
  waitingRun,
} from "@/design-system/fixtures/shared-ingestion";

const meta = {
  title: "Shared/Ingestion/Run status",
  component: RunStatus,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The state of a run in a sentence or three, read out as it changes: the top of Run progress, in Fill my profile and the welcome wizard.",
      },
    },
  },
  args: { run: runningRun() },
} satisfies Meta<typeof RunStatus>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Which document is read now, and which step of it. */
export const Reading: Story = { name: "Reading a document" };

/** Waiting for a place among the person's agent runs. */
export const Waiting: Story = {
  name: "Waiting for its turn",
  args: { run: waitingRun() },
};

/** Cancel was clicked: it ends after the step in progress. */
export const Stopping: Story = {
  name: "Stopping",
  args: { run: stoppingRun() },
};

/** At its limit: what it used, what Continue adds and the limit it sets, and what silence does. */
export const PausedAtTheLimit: Story = {
  name: "Paused at its token limit",
  args: { run: pausedRun() },
};

/** At its limit with nothing left: it closes by itself. */
export const Closing: Story = {
  name: "At its limit, closing by itself",
  args: { run: closingRun() },
};

/** Paused by a failure: the reason, and what Resume does. */
export const PausedByAFailure: Story = {
  name: "Paused by a failure",
  args: { run: pausedOnFailureRun() },
};

/** Everything read. */
export const Finished: Story = {
  name: "Finished",
  args: { run: doneRun() },
};

/** Finished with documents left unread. */
export const Partial: Story = {
  name: "Finished, not everything read",
  args: { run: partialRun() },
};

/** Closed by the engine, with its reason. */
export const Failed: Story = {
  name: "Failed",
  args: { run: failedRun() },
};

/** Cancelled: nothing it read was added. */
export const Cancelled: Story = {
  name: "Cancelled",
  args: { run: cancelledRun() },
};
