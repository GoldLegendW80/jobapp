import type { Meta, StoryObj } from "@storybook/react-vite";
import { MemoryRouter } from "react-router";

import { PostingNotices } from "@/components/shared/jobs/posting/posting-notices";
import { NOTICES } from "@/design-system/fixtures/posting-notices";

const meta = {
  title: "Shared/Jobs/Posting notices",
  component: PostingNotices,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "What to know about a posting before the click: what its text says against the person's profile (sponsorship, the right to work, a degree, a contractor role, a fixed term, unpaid work, a fee, a pay under the aim), its closing date, a posting of another company with the same text, and the person's applications to the company. Facts, never a block. In full under the posting page's header, with the posting's own words and a link to the other posting; compact on the Discover card and in the Apply confirmation.",
      },
    },
  },
  decorators: [
    (Story) => (
      <MemoryRouter>
        <div className="max-w-2xl">
          <Story />
        </div>
      </MemoryRouter>
    ),
  ],
  args: { notices: NOTICES },
} satisfies Meta<typeof PostingNotices>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Under the posting page's header: the warnings first, each with the posting's own words. */
export const Full: Story = { name: "On the posting page" };

/** On the Discover card: the first two sentences, the rest counted. */
export const Compact: Story = {
  name: "On the Discover card",
  args: { compact: true, max: 2 },
  decorators: [
    (Story) => (
      <div className="max-w-[24rem]">
        <Story />
      </div>
    ),
  ],
};

/** A posting with one thing to know: its closing date, a week away. */
export const OneNotice: Story = {
  name: "One thing to know",
  args: {
    notices: [
      {
        kind: "closing",
        code: "closes_on",
        text: "This posting closes on 17 November 2026, in 7 days.",
        quote: "Applications close on 17 November 2026.",
        level: "warning",
        job_id: null,
      },
    ],
  },
};

/** Nothing to know: nothing shows, not even an empty frame. */
export const Nothing: Story = {
  name: "Nothing to know",
  args: { notices: [] },
};
