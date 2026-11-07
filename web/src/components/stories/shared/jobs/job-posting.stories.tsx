import type { Meta, StoryObj } from "@storybook/react-vite";
import { Maximize2 } from "lucide-react";
import { Link } from "react-router";

import { JobPosting } from "@/components/shared/jobs/posting/job-posting";
import { Button } from "@/components/ui/button";
import {
  delayPostingDetail,
  failPostingDetail,
  fakePostingDetails,
  LONG_TITLE_POSTING,
  NO_PAY_NO_FIT_POSTING,
} from "@/design-system/fixtures/posting-details";
import {
  fakeJobFiles,
  WORKED_ON_POSTING,
} from "@/design-system/fixtures/posting-parts";
import { POSTING_DETAIL, POSTINGS } from "@/design-system/fixtures/postings";
import { withApp } from "@/design-system/with-app";
import { WORDING } from "@/lib/wording";
import { fakeSession, member } from "@/test/auth";
import { fakeJobDetail } from "@/test/jobs";

const meta = {
  title: "Shared/Jobs/Job posting",
  component: JobPosting,
  tags: ["autodocs"],
  parameters: {
    layout: "fullscreen",
    docs: {
      description: {
        component:
          "One posting, loaded by id: its text, notes, files and history in the main column, the application, documents, status and fit, assessment and details in the side rail. The posting page and the Jobs list's pane both show it.",
      },
      story: { inline: false, height: "1000px" },
    },
  },
  decorators: [
    withApp,
    (Story) => (
      <div className="p-4 sm:p-6">
        <Story />
      </div>
    ),
  ],
  args: { id: POSTING_DETAIL.id, trail: [{ to: "/jobs", label: "Jobs" }] },
  beforeEach: () => {
    fakeSession(member);
    fakePostingDetails([
      ...POSTINGS,
      LONG_TITLE_POSTING,
      NO_PAY_NO_FIT_POSTING,
    ]);
  },
} satisfies Meta<typeof JobPosting>;

export default meta;
type Story = StoryObj<typeof meta>;

/** On its own page, under the breadcrumb: Jobs, then the posting. */
export const OnItsPage: Story = { name: "On its page" };

/** In the Jobs list's pane: the title is an h2, Open full page comes first. */
export const InThePane: Story = {
  name: "In the list's pane",
  args: {
    trail: undefined,
    titleLevel: 2,
    actions: (
      <Button asChild variant="outline" size="sm">
        <Link to={`/jobs/${POSTING_DETAIL.id}`}>
          <Maximize2 aria-hidden="true" />
          {WORDING.openFullPage}
        </Link>
      </Button>
    ),
  },
};

/** A posting worked on for a few days: notes, files, a long history, an imported field. */
export const WorkedOn: Story = {
  name: "Worked on for a few days",
  beforeEach: () => {
    fakeJobDetail(WORKED_ON_POSTING);
    fakeJobFiles();
  },
};

/** A long title and company wrap in the header. */
export const LongTitle: Story = {
  name: "A long title",
  args: { id: LONG_TITLE_POSTING.id },
};

/** No pay and no fit yet: both say so plainly. */
export const NoPayNoFit: Story = {
  name: "No pay and no fit",
  args: { id: NO_PAY_NO_FIT_POSTING.id },
};

/** While it loads, its header and sections hold their place. */
export const Loading: Story = {
  name: "Loading",
  beforeEach: () => {
    delayPostingDetail(POSTING_DETAIL.id, 10_000);
  },
};

/** A posting deleted meanwhile: the pane says so instead of an error. */
export const Deleted: Story = {
  name: "A deleted posting",
  beforeEach: () => {
    failPostingDetail(POSTING_DETAIL.id, 404);
  },
};

/** When it cannot be loaded: what failed, and Retry. */
export const LoadFails: Story = {
  name: "Could not be loaded",
  beforeEach: () => {
    failPostingDetail(POSTING_DETAIL.id, 500);
  },
};

/** On a phone the side rail comes after the posting, the application first. */
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
