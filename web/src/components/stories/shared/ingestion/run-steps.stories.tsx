import type { Meta, StoryObj } from "@storybook/react-vite";

import { RunSteps } from "@/components/shared/ingestion/run-progress/run-steps";
import { runningRun } from "@/design-system/fixtures/camille-ingestion";
import { partialRun } from "@/design-system/fixtures/shared-ingestion";
import { fakeRun, fakeStep } from "@/test/ingestion-runs";

const meta = {
  title: "Shared/Ingestion/Run steps",
  component: RunSteps,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Every step of a run, one Trace line each: its document, then what it does, what it found and what its last attempt cost. A step at work breathes, one waiting is dimmed, one not read is red. In the fold Every step of this run, at the bottom of Run progress.",
      },
    },
  },
  args: { run: runningRun() },
} satisfies Meta<typeof RunSteps>;

export default meta;
type Story = StoryObj<typeof meta>;

/** While the run reads: one step read, one being read. */
export const WhileItReads: Story = { name: "While the run reads" };

/** An ended run: a step read, one not read with its error, one of a deleted document. */
export const WithFailures: Story = {
  name: "With a step not read and a deleted document",
  args: { run: partialRun() },
};

/** A long document read in parts, and its page images. */
export const ManyParts: Story = {
  name: "A long document read in parts",
  args: {
    run: fakeRun({
      document_ids: ["d1"],
      steps: [
        ...[0, 1, 2].map((part) =>
          fakeStep({
            id: `p${String(part)}`,
            position: part,
            part,
            status: part < 2 ? "done" : "running",
            items: part < 2 ? 4 : 0,
            tokens: part < 2 ? 22_000 : null,
            document_name:
              "camille-martin-portfolio-2019-to-2026-with-every-project.pdf",
          }),
        ),
        fakeStep({
          id: "t0",
          position: 3,
          kind: "transcribe",
          status: "planned",
          document_name:
            "camille-martin-portfolio-2019-to-2026-with-every-project.pdf",
        }),
      ],
    }),
  },
};

/** On a phone a long name is cut short and the sentence under it wraps. */
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
