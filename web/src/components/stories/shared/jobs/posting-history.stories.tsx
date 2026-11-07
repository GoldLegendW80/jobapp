import type { Meta, StoryObj } from "@storybook/react-vite";

import { PostingHistory } from "@/components/shared/jobs/posting/posting-history";
import { WORKED_ON_POSTING } from "@/design-system/fixtures/posting-parts";
import { postingDetail } from "@/design-system/fixtures/posting-details";
import { POSTING_DETAIL } from "@/design-system/fixtures/postings";

const meta = {
  title: "Shared/Jobs/Posting history",
  component: PostingHistory,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "What happened to a posting, newest first: found, rescored, edited, moved down the pipeline, noted. In the main column of a posting, under its files.",
      },
    },
  },
  args: { job: postingDetail(POSTING_DETAIL) },
} satisfies Meta<typeof PostingHistory>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Just found by a search. */
export const JustFound: Story = { name: "Just found" };

/** Every kind of event, and a note that more are kept than shown. */
export const Long: Story = {
  name: "A long history",
  args: { job: WORKED_ON_POSTING },
};

/** No event at all, as an old import can be. */
export const Empty: Story = {
  name: "No events yet",
  args: { job: { ...WORKED_ON_POSTING, events: [], events_total: 0 } },
};

/** At a phone's width the lines wrap beside the timeline. */
export const OnAPhone: Story = {
  name: "On a phone",
  args: { job: WORKED_ON_POSTING },
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
};
