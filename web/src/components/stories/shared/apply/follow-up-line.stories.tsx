import type { Meta, StoryObj } from "@storybook/react-vite";

import { FollowUpLine } from "@/components/shared/apply/follow-up-line";
import {
  cold,
  fakeFollowUps,
  followUp,
  waiting,
} from "@/design-system/fixtures/follow-ups";
import { withApp } from "@/design-system/with-app";

const meta = {
  title: "Shared/Apply/Follow up line",
  component: FollowUpLine,
  tags: ["autodocs"],
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
    withApp,
  ],
  parameters: {
    docs: {
      description: {
        component:
          "On a posting the person applied to: where its follow ups stand and their dates, with the way to Applications, where the draft is written. Nothing for a posting that is not followed, or while follow ups are off.",
      },
    },
  },
} satisfies Meta<typeof FollowUpLine>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The first follow up is due. */
export const Due: Story = {
  args: { jobId: "posting-fu-1" },
  beforeEach: () => {
    fakeFollowUps([followUp()]);
  },
};

/** The next one waits for its day. */
export const Waiting: Story = {
  args: { jobId: "posting-fu-2" },
  beforeEach: () => {
    fakeFollowUps([waiting()]);
  },
};

/** Two follow ups got no answer: no third is suggested. */
export const NoAnswerAfterTwo: Story = {
  name: "No answer after two",
  args: { jobId: "posting-fu-3" },
  beforeEach: () => {
    fakeFollowUps([cold()]);
  },
};

/** A thank you note the day after an interview. */
export const ThankYouNote: Story = {
  args: { jobId: "posting-fu-1" },
  beforeEach: () => {
    fakeFollowUps([
      followUp({
        kind: "thank_you",
        status: "interview",
        next_on: "2026-11-07",
      }),
    ]);
  },
};
