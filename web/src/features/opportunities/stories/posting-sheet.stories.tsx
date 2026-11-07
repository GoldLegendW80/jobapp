import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { DECK } from "@/design-system/fixtures/discover";
import { POSTING_DETAIL } from "@/design-system/fixtures/postings";
import { withApp } from "@/design-system/with-app";
import { fakeJobDetail } from "@/test/jobs";
import { makeCard } from "@/test/deck";

import { PostingSheet } from "../components/deck/posting-sheet";

const meta = {
  title: "Pages/Discover/Posting sheet",
  component: PostingSheet,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "More on a deck card of Discover: the whole posting, read when it opens. A sheet from the right beside the deck; on a phone a drawer from the bottom, dragged down by its handle to close.",
      },
    },
  },
  args: { card: DECK[0] ?? makeCard(1), open: true, onOpenChange: fn() },
} satisfies Meta<typeof PostingSheet>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The posting's text, with the links to its page and its source. */
export const ReadThePosting: Story = {
  beforeEach: () => {
    fakeJobDetail({
      ...POSTING_DETAIL,
      posting_text: POSTING_DETAIL.description,
    });
  },
};

/** No text was saved for this posting. */
export const NoTextSaved: Story = {
  beforeEach: () => {
    fakeJobDetail({ ...POSTING_DETAIL, posting_text: null });
  },
};
