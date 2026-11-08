import type { Meta, StoryObj } from "@storybook/react-vite";

import { AssessmentDetails } from "@/components/shared/jobs/assess/assessment-details";
import {
  ASSESSMENT_RESULT,
  assessmentView,
  fakeAssessment,
} from "@/design-system/fixtures/posting-assessment";
import { POSTING_DETAIL } from "@/design-system/fixtures/postings";
import { withApp } from "@/design-system/with-app";

const meta = {
  title: "Shared/Jobs/Assessment",
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "The posting's last assessment, under the posting and folded: the requirements it weighs with where the person stands, the person's own criteria, what the text shows, and what it says about the role, with the questions to ask when it says too little. Absent before the first assessment.",
      },
      story: { inline: false, height: "640px" },
    },
  },
  render: () => (
    <div className="max-w-3xl">
      <AssessmentDetails job={POSTING_DETAIL} />
    </div>
  ),
  beforeEach: () => {
    fakeAssessment();
  },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** Folded: the date and the verdict, and one button that opens the rest. */
export const Folded: Story = {};

/** A posting in a language the role check does not read, with no criteria in the profile. */
export const LittleToRead: Story = {
  beforeEach: () => {
    fakeAssessment(
      assessmentView({
        assessment: {
          run_id: null,
          created_at: "2026-11-07T08:30:00Z",
          result: {
            ...ASSESSMENT_RESULT,
            culture: [],
            signals: [],
            more_requirements: 0,
            role_clarity: { read: false, points: [], questions: [] },
          },
        },
      }),
    );
  },
};
