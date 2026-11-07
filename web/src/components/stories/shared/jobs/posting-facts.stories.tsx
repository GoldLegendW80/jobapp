import type { Meta, StoryObj } from "@storybook/react-vite";

import { PostingFacts } from "@/components/shared/jobs/posting/posting-facts";
import { POSTING_DETAIL } from "@/design-system/fixtures/postings";

const LINK = "https://careers.example.org/1";

const meta = {
  title: "Shared/Jobs/Posting facts",
  component: PostingFacts,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "A posting's dates (found, published, applied) and where it was found, as a list of facts: the Details section at the end of a posting's side rail.",
      },
    },
  },
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
  args: {
    job: {
      ...POSTING_DETAIL,
      published_on: "2026-10-20",
      applied_on: "2026-11-04",
      source: "ashby:lumen-freight",
    },
    link: LINK,
  },
} satisfies Meta<typeof PostingFacts>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Found, published, applied, and the company's job board. */
export const Typical: Story = { name: "Every date and the source" };

/** Found by a freelance feed first: "via" links back to the posting there, as the feed asks. */
export const ViaAFeed: Story = {
  name: "Found by a feed",
  args: {
    job: {
      ...POSTING_DETAIL,
      contract_type: "Freelance",
      source: "remoteok, ashby:lumen-freight",
    },
  },
};

/** Only the date it was found, as for a posting added by hand. */
export const FoundOnly: Story = {
  name: "Only the date found",
  args: {
    job: {
      ...POSTING_DETAIL,
      published_on: null,
      applied_on: null,
      source: null,
    },
    link: null,
  },
};
