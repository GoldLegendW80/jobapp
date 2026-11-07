import type { Meta, StoryObj } from "@storybook/react-vite";

import { SweepJobFeeds } from "@/components/shared/jobs/job-feeds-ui";
import type { SweepSource } from "@/features/jobs/api/sweeps-api";

/** A job feed's line of a search's summary. */
function feed(kind: string, fields: Partial<SweepSource> = {}): SweepSource {
  return {
    mode: "api",
    kind,
    query: "data engineer",
    location: "Lyon",
    result_pages: 1,
    found: 0,
    new: 0,
    skipped_by_rule: 0,
    items_skipped: 0,
    error: null,
    ...fields,
  };
}

const meta = {
  title: "Shared/Jobs/Sweep job feeds",
  component: SweepJobFeeds,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "What each job feed (Arbeitnow, Himalayas, Adzuna) gave a search, or why it gave nothing: in the summary of a search, after Find jobs.",
      },
    },
  },
  args: {
    sources: [
      feed("arbeitnow", { searches: 2, found: 14, new: 6 }),
      feed("himalayas", { searches: 1, found: 3, new: 1 }),
      feed("adzuna", { searches: 2, found: 22, new: 9 }),
    ],
  },
} satisfies Meta<typeof SweepJobFeeds>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Three feeds read: their searches, the postings kept and the new ones. */
export const Typical: Story = { name: "Three feeds read" };

/** Adzuna skipped for want of a key, with where to add one. */
export const NoKey: Story = {
  name: "Adzuna skipped without a key",
  args: {
    sources: [
      feed("arbeitnow", { searches: 2, found: 14, new: 6 }),
      feed("adzuna", { reason: "no_key" }),
    ],
  },
};

/** A feed that failed says why, and what was read is kept. */
export const Failed: Story = {
  name: "A feed that failed",
  args: {
    sources: [
      feed("arbeitnow", {
        searches: 2,
        found: 4,
        new: 1,
        error: "rate_limited",
      }),
      feed("himalayas", { error: "timeout" }),
    ],
  },
};

/** At a phone's width the lines wrap. */
export const OnAPhone: Story = {
  name: "On a phone",
  args: NoKey.args,
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
};
