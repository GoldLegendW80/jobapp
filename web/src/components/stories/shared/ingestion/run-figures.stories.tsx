import type { Meta, StoryObj } from "@storybook/react-vite";

import { RunFigures } from "@/components/shared/ingestion/run-progress/run-figures";
import {
  doneRun,
  runningRun,
} from "@/design-system/fixtures/camille-ingestion";
import { partialRun } from "@/design-system/fixtures/shared-ingestion";
import { fakeRun } from "@/test/ingestion-runs";

const meta = {
  title: "Shared/Ingestion/Run figures",
  component: RunFigures,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The figures of a run: steps or documents read, what was found, the tokens, the cost on an API key and the proposals: in Run progress.",
      },
    },
  },
  args: { run: runningRun() },
} satisfies Meta<typeof RunFigures>;

export default meta;
type Story = StoryObj<typeof meta>;

/** While it reads: steps read, found so far, tokens and where it pauses, and why the figure lags. */
export const WhileItReads: Story = { name: "While it reads" };

/** While it reads on an API key, with steps reused and one not read. */
export const OnAnApiKey: Story = {
  name: "While it reads, on an API key",
  args: {
    run: fakeRun({
      cost_usd: "0.38",
      counts: {
        steps: 6,
        done: 3,
        reused: 2,
        failed: 1,
        items: 14,
        refused: 2,
      },
    }),
  },
};

/** Finished: documents read and the proposals to review. */
export const Finished: Story = {
  name: "Finished",
  args: { run: doneRun() },
};

/** Finished with documents unread, the cost of every attempt, and dates it could not read. */
export const Partial: Story = {
  name: "Finished, not everything read",
  args: { run: partialRun() },
};

/** On a phone the figures go in one column. */
export const OnAPhone: Story = {
  name: "On a phone",
  args: { run: partialRun() },
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
};
