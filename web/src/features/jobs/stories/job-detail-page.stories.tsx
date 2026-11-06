import type { Meta, StoryObj } from "@storybook/react-vite";

import { AppAt } from "@/design-system/app-at";
import {
  delayPostingDetail,
  failPostingDetail,
  fakePostingDetails,
  LONG_TITLE_POSTING,
  NO_PAY_NO_FIT_POSTING,
} from "@/design-system/fixtures/posting-details";
import { POSTING_DETAIL, POSTINGS } from "@/design-system/fixtures/postings";
import { member, fakeSession } from "@/test/auth";
import { unfoldPostingMore } from "@/test/jobs";

/** "More about this posting" keeps its open state in local storage: each story starts folded. */
const FOLD_KEY = "advanced:posting-more";
const fold = () => {
  window.localStorage.removeItem(FOLD_KEY);
};

/** The first invented posting, Senior data engineer at Lumen Freight. */
const first = POSTING_DETAIL;

const meta = {
  title: "Pages/Jobs/Posting",
  parameters: {
    layout: "fullscreen",
    page: true,
    docs: {
      description: {
        component:
          "One posting, in the order a person applies: the header with the pay, fit and status, then Your application (Apply and the CV and letter), the posting's text, and the rest folded under More about this posting. Route /jobs/:id; the Jobs list's detail pane shows the same content.",
      },
    },
  },
  beforeEach: () => {
    fold();
    fakeSession(member);
    fakePostingDetails([
      ...POSTINGS,
      LONG_TITLE_POSTING,
      NO_PAY_NO_FIT_POSTING,
    ]);
    return fold;
  },
  render: () => <AppAt path={`/jobs/${first.id}`} />,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** As a job seeker, I want a posting's pay, fit, skills and text on one page with Apply first, so that I can decide and apply without hunting. */
export const ReadAPosting: Story = {};

/** As a job seeker, I want the posting's status, notes, files and history at hand when I need them, so that the page stays short until I do. */
export const SeeEverythingAboutAPosting: Story = {
  beforeEach: () => {
    unfoldPostingMore();
  },
};

/** As a job seeker, I want a long title and company to wrap in full, so that I know exactly which role this is. */
export const ReadALongTitle: Story = {
  render: () => <AppAt path={`/jobs/${LONG_TITLE_POSTING.id}`} />,
};

/** As a job seeker, I want a posting without pay or score to say so plainly, so that I never mistake a gap for a figure. */
export const SeeAPostingWithoutPayOrFit: Story = {
  render: () => <AppAt path={`/jobs/${NO_PAY_NO_FIT_POSTING.id}`} />,
};

/** As a job seeker, I want to see the posting is on its way, so that a slow answer never looks like an empty page. */
export const WaitForThePosting: Story = {
  beforeEach: () => {
    delayPostingDetail(first.id, 10_000);
  },
};

/** As a job seeker, I want to know when a posting could not be loaded, so that I can try again. */
export const PostingUnavailable: Story = {
  beforeEach: () => {
    failPostingDetail(first.id, 500);
  },
};

/** As a job seeker, I want an old link to a deleted posting to say the page is gone, so that I can go back to my list. */
export const PostingNoLongerExists: Story = {
  beforeEach: () => {
    failPostingDetail(first.id, 404);
  },
};

/** As a job seeker, I want the posting in one column on my phone with Apply near the top, so that I can apply on the go. */
export const ReadAPostingOnMyPhone: Story = {
  globals: { viewport: { value: "mobile2", isRotated: false } },
};
