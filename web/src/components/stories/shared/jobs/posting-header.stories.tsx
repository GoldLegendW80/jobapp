import type { Meta, StoryObj } from "@storybook/react-vite";
import { Maximize2 } from "lucide-react";
import { Link } from "react-router";

import { PostingHeader } from "@/components/shared/jobs/posting/posting-header";
import { Button } from "@/components/ui/button";
import { EditableJob } from "@/design-system/fixtures/job-harnesses";
import {
  LONG_TITLE_POSTING,
  NO_PAY_NO_FIT_POSTING,
  postingDetail,
} from "@/design-system/fixtures/posting-details";
import { POSTING_DETAIL } from "@/design-system/fixtures/postings";
import { withApp } from "@/design-system/with-app";
import type { JobDetail } from "@/features/jobs/api/jobs-api";
import { WORDING } from "@/lib/wording";
import { fakeEditableJob } from "@/test/jobs";

/** The header of `job` on its page (h1, under the breadcrumb) or in the list's pane (h2). */
function Header({ job, level }: { job: JobDetail; level: 1 | 2 }) {
  return (
    <EditableJob id={job.id}>
      {(current, save) => (
        <PostingHeader
          job={current}
          save={save}
          titleId="posting-title"
          level={level}
          trail={level === 1 ? [{ to: "/jobs", label: "Jobs" }] : undefined}
          actions={
            level === 2 ? (
              <Button asChild variant="outline" size="sm">
                <Link to={`/jobs/${current.id}`}>
                  <Maximize2 aria-hidden="true" />
                  {WORDING.openFullPage}
                </Link>
              </Button>
            ) : undefined
          }
        />
      )}
    </EditableJob>
  );
}

const meta = {
  title: "Shared/Jobs/Posting header",
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "The top of a posting: its title, the company, place and pay on one line, then Chat and Edit details. The page's h1 on a posting's page, an h2 in the Jobs list's pane.",
      },
      story: { inline: false, height: "240px" },
    },
  },
  beforeEach: () => {
    fakeEditableJob(POSTING_DETAIL);
  },
  render: () => <Header job={POSTING_DETAIL} level={1} />,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** On its own page: the breadcrumb, Jobs then the posting, above the title. */
export const OnItsPage: Story = { name: "On its page" };

/** In the Jobs list's pane: an h2, Open full page first. */
export const InThePane: Story = {
  name: "In the list's pane",
  render: () => <Header job={POSTING_DETAIL} level={2} />,
};

/** A posting that states no pay says so in the line under the title. */
export const NoPay: Story = {
  name: "Pay not given",
  beforeEach: () => {
    fakeEditableJob(postingDetail(NO_PAY_NO_FIT_POSTING));
  },
  render: () => <Header job={postingDetail(NO_PAY_NO_FIT_POSTING)} level={1} />,
};

/** A long title and company wrap; the buttons go under them. */
export const LongTitle: Story = {
  name: "A long title",
  beforeEach: () => {
    fakeEditableJob(postingDetail(LONG_TITLE_POSTING));
  },
  render: () => <Header job={postingDetail(LONG_TITLE_POSTING)} level={1} />,
};

/** On a phone the buttons go under the title. */
export const OnAPhone: Story = {
  name: "On a phone",
  render: () => (
    <div className="max-w-sm">
      <Header job={POSTING_DETAIL} level={1} />
    </div>
  ),
};
