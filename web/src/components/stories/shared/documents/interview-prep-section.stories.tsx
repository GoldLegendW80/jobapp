import type { Meta, StoryObj } from "@storybook/react-vite";

import { InterviewPrepSection } from "@/components/shared/documents/interview-prep/interview-prep-section";
import {
  NOT_PREPARED,
  fakeInterviewPrep,
  preparedFor,
} from "@/design-system/fixtures/interview-prep";
import { POSTING_DETAIL } from "@/design-system/fixtures/postings";
import { withApp } from "@/design-system/with-app";

const INVITED = { ...POSTING_DETAIL, status: "interview" as const };

const meta = {
  title: "Shared/Documents/Interview preparation",
  component: InterviewPrepSection,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    at: `/jobs/${POSTING_DETAIL.id}`,
    docs: {
      description: {
        component:
          "The Interview section of a posting's page: Prepare for the interview on a click, first once the posting's status is interview and on demand before it; once prepared, its state and the link to the preparation.",
      },
    },
  },
  args: { job: INVITED },
} satisfies Meta<typeof InterviewPrepSection>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Invited to an interview: Prepare is the primary action. */
export const Invited: Story = {
  beforeEach: () => {
    fakeInterviewPrep(INVITED, NOT_PREPARED, { consent: { accepted: true } });
  },
};

/** Not invited yet: Prepare stays on offer, on demand. */
export const OnDemand: Story = {
  name: "Before an invitation",
  args: { job: POSTING_DETAIL },
  beforeEach: () => {
    fakeInterviewPrep(POSTING_DETAIL, NOT_PREPARED);
  },
};

/** Prepared: its counts and the link to the page. */
export const Prepared: Story = {
  beforeEach: () => {
    fakeInterviewPrep(INVITED, preparedFor());
  },
};
