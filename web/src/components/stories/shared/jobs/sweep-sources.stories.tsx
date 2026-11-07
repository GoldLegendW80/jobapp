import type { Meta, StoryObj } from "@storybook/react-vite";

import { SweepSources } from "@/components/shared/jobs/sweep-sources";
import { websiteOfSweep } from "@/design-system/fixtures/sweep-progress";

const meta = {
  title: "Shared/Jobs/Websites searched",
  component: SweepSources,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "In the summary of a search (Find jobs, Past searches): each source searched, what it found, and why one ended early, in words.",
      },
    },
  },
  args: {
    sources: [
      { ...websiteOfSweep("Welcome to the Jungle", "done"), searches: 4 },
      {
        ...websiteOfSweep("Free-Work", "done"),
        found: 0,
        new: 0,
        outcome: "no_match",
      },
      {
        ...websiteOfSweep("Indeed", "done"),
        found: 0,
        new: 0,
        error: "security_check",
      },
    ],
  },
} satisfies Meta<typeof SweepSources>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Three websites: one found postings, one found none that matched, one stopped on a check. */
export const ThreeWebsites: Story = { name: "Three websites" };

/** Nothing searched: the summary shows no list. */
export const NoSource: Story = { name: "No source", args: { sources: [] } };

/** On a phone the lines wrap. */
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
