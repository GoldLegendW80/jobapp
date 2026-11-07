import type { Meta, StoryObj } from "@storybook/react-vite";

import { PostingContent } from "@/components/shared/jobs/posting/posting-content";
import {
  LONG_TITLE_POSTING,
  postingDetail,
} from "@/design-system/fixtures/posting-details";
import { WORKED_ON_POSTING } from "@/design-system/fixtures/posting-parts";
import { POSTING_DETAIL } from "@/design-system/fixtures/postings";

const LINK = "https://careers.example.org/1";

const meta = {
  title: "Shared/Jobs/Posting content",
  component: PostingContent,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The posting itself: its status, fit, contract and work mode, its text in short lines, the competencies and gaps behind the fit, and the link to the original. The first section of a posting's main column.",
      },
    },
  },
  args: { job: postingDetail(POSTING_DETAIL), link: LINK },
} satisfies Meta<typeof PostingContent>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A posting found by a search, with its text. */
export const Typical: Story = { name: "A posting with its text" };

/** A fit set by hand and a staged posting. */
export const WorkedOn: Story = {
  name: "Staged, fit set by hand",
  args: { job: WORKED_ON_POSTING },
};

/** No text saved: the summary stands in, and the page says so. */
export const NoText: Story = {
  name: "No posting text, a summary",
  args: {
    job: {
      ...POSTING_DETAIL,
      posting_text: null,
      description:
        "Own the pricing team's pipelines, from tracking events to the planners' tables.",
    },
  },
};

/** Nothing to read at all, no competency, no gap. */
export const Bare: Story = {
  name: "No text and no terms",
  args: {
    job: {
      ...POSTING_DETAIL,
      posting_text: "",
      description: null,
      competencies: [],
      gaps: [],
      work_mode: null,
      fit: null,
    },
  },
};

/** An address that is not a web page is shown as text, never as a link. */
export const UnsafeLink: Story = {
  name: "A link that is not a web address",
  args: {
    job: { ...POSTING_DETAIL, url: "javascript:alert('example')" },
    link: null,
  },
};

/** A long text keeps short lines; long terms wrap. */
export const Long: Story = {
  name: "A long posting",
  args: { job: postingDetail(LONG_TITLE_POSTING) },
};

/** On a phone. */
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
