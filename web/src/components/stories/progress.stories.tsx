import type { Meta, StoryObj } from "@storybook/react-vite";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";

function Labelled({
  label,
  count,
  value,
}: {
  label: string;
  count: string;
  value: number;
}) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-baseline justify-between gap-4 text-sm">
        <span>{label}</span>
        <span className="shrink-0 text-muted-foreground tabular-nums">
          {count}
        </span>
      </div>
      <Progress value={value} aria-label={`${label}, ${count}`} />
    </div>
  );
}

const meta = {
  title: "Components/Feedback/Progress",
  component: Progress,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "How far a task with a known end has come: documents read, forms filled, a profile completed. Always beside words that say what and how much; use Spinner when the end is unknown.",
      },
    },
  },
  args: { value: 38, "aria-label": "Reading your documents, 3 of 8" },
  argTypes: { value: { control: { type: "range", min: 0, max: 100 } } },
} satisfies Meta<typeof Progress>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A reading of the person's documents: what runs, and how many of how many. */
export const Typical: Story = {
  render: () => (
    <div className="max-w-md">
      <Labelled
        label="Reading your documents"
        count="3 of 8"
        value={(3 / 8) * 100}
      />
    </div>
  ),
};

/** The bar alone, for the controls panel. Give it an accessible name all the same. */
export const BarOnly: Story = {
  render: (args) => (
    <div className="max-w-md">
      <Progress {...args} />
    </div>
  ),
};

/** Start, middle and end: the track shows from zero, the bar fills it at the end. */
export const Values: Story = {
  render: () => (
    <div className="flex max-w-md flex-col gap-5">
      <Labelled label="Waiting to start" count="0 of 8" value={0} />
      <Labelled label="Reading your documents" count="4 of 8" value={50} />
      <Labelled label="Done: 8 documents read" count="8 of 8" value={100} />
    </div>
  ),
};

/** Inside a card: how complete the profile is, and what is left to fill. */
export const InCard: Story = {
  render: () => (
    <Card className="max-w-md">
      <CardHeader>
        <CardTitle>Your profile</CardTitle>
        <CardDescription>
          Add your languages and your pay to finish it.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Labelled label="Complete" count="7 of 9 sections" value={78} />
      </CardContent>
    </Card>
  ),
};

/** Many at once, one per document. Each says its own state in words. */
export const Many: Story = {
  render: () => (
    <div className="flex max-w-md flex-col gap-5">
      <Labelled label="Base CV" count="Done" value={100} />
      <Labelled label="LinkedIn data export" count="2 of 3 parts" value={67} />
      <Labelled
        label="Reference letter, scanned"
        count="Page 1 of 4"
        value={25}
      />
      <Labelled label="Portfolio" count="Waiting" value={0} />
    </div>
  ),
};

/** A long label wraps above the bar; the count keeps its place on the right. */
export const LongLabel: Story = {
  render: () => (
    <div className="max-w-xs">
      <Labelled
        label="Completing your profile on Welcome to the Jungle"
        count="2 of 5"
        value={40}
      />
    </div>
  ),
};
