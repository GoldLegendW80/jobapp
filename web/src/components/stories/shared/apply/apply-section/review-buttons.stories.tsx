import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { ReviewButtons } from "@/components/shared/apply/apply-section/review-buttons";
import { FILLED, ONE_LEFT } from "@/design-system/fixtures/applications";
import {
  APPLY_JOB,
  approvalOf,
  OPTIONAL_LEFT,
} from "@/design-system/fixtures/shared-apply";
import { leftAnswer } from "@/test/apply";
import { withButtonRow } from "./button-row";

const meta = {
  title: "Shared/Apply/Apply section/Review buttons",
  component: ReviewButtons,
  tags: ["autodocs"],
  decorators: [withButtonRow],
  parameters: {
    docs: {
      description: {
        component:
          "Approve and submit, which opens the last confirmation, and Reject, under a filled form waiting for the person in Your application. Approve waits while an answer is typed and not saved, or a required one is left.",
      },
    },
  },
  args: {
    job: APPLY_JOB,
    approval: approvalOf(FILLED),
    busy: false,
    unsaved: 0,
    onApprove: fn(),
    onReject: fn(),
  },
} satisfies Meta<typeof ReviewButtons>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Every required field answered: Approve and submit sends the application to the company. */
export const ReadyToApprove: Story = {
  name: "Ready to approve",
};

/** Optional fields left empty do not hold Approve: they are sent empty. */
export const OptionalLeftEmpty: Story = {
  name: "Optional fields left empty",
  args: { approval: approvalOf(OPTIONAL_LEFT) },
};

/** A required question is left: Approve waits and says which. */
export const ARequiredQuestionLeft: Story = {
  name: "A required question is left",
  args: { approval: approvalOf(ONE_LEFT) },
};

/** Several required questions are left: all are named. */
export const SeveralLeft: Story = {
  name: "Several required questions left",
  args: {
    approval: approvalOf([
      ...ONE_LEFT,
      leftAnswer("salary", "Salary expectations", { required: true }),
    ]),
  },
};

/** An answer is typed and not saved: only saved answers are sent. */
export const AnswerNotSaved: Story = {
  name: "An answer is not saved",
  args: { unsaved: 1 },
};

/** While another action is on its way, both wait. */
export const Busy: Story = {
  name: "Busy",
  args: { busy: true },
};

/** On a phone, the buttons stack and take the whole width. */
export const OnAPhone: Story = {
  name: "On a phone",
  parameters: { phone: true },
};
