import type { Meta, StoryObj } from "@storybook/react-vite";

import { PostingMatch } from "@/components/shared/jobs/posting/posting-match";
import {
  MATCH,
  MATCH_BANDS,
  MATCH_LONGEST,
  MATCH_OLDER_TEXT,
} from "@/design-system/fixtures/posting-match";

const meta = {
  title: "Shared/Jobs/Posting match",
  component: PostingMatch,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The assistant's reading of a posting against the career and culture the profile states: two scores with a band, strengths and gaps in text, and the day it was read. Apart from the keyword fit. A posting with no reading shows nothing.",
      },
    },
  },
  args: { match: MATCH },
} satisfies Meta<typeof PostingMatch>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A reading of the posting's current text. */
export const Typical: Story = { name: "A reading" };

/** The text changed since: the reading stays, with one line that says so. */
export const OlderText: Story = {
  name: "A reading on an older text",
  args: { match: MATCH_OLDER_TEXT },
};

/** Three strengths and three gaps at 120 characters: long words wrap. */
export const Longest: Story = {
  name: "The longest strengths and gaps",
  args: { match: MATCH_LONGEST },
};

/** The edges of the four bands. */
export const Bands: Story = {
  name: "A score in each band",
  render: () => (
    <div className="flex flex-col gap-8">
      {MATCH_BANDS.map((match) => (
        <PostingMatch key={match.career} match={match} />
      ))}
    </div>
  ),
};

/** On a phone, and in the Jobs list's pane at its narrowest: the block's own width decides, so the lists are stacked. */
export const OnAPhone: Story = {
  name: "On a phone",
  args: { match: MATCH_LONGEST },
  decorators: [
    (Story) => (
      <div className="w-80 max-w-full">
        <Story />
      </div>
    ),
  ],
};
