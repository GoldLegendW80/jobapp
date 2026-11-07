import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { FilledForm } from "@/components/shared/apply/apply-section/filled-form";
import { fakeFormShots, ONE_LEFT } from "@/design-system/fixtures/applications";
import {
  applicationIn,
  approvalOf,
} from "@/design-system/fixtures/shared-apply";
import { ApiError } from "@/lib/api";

const meta = {
  title: "Shared/Apply/Apply section/Filled form",
  component: FilledForm,
  tags: ["autodocs"],
  decorators: [
    (Story, { parameters }) => (
      <div
        className={
          parameters.phone ? "@container max-w-80" : "@container max-w-3xl"
        }
      >
        <Story />
      </div>
    ),
  ],
  parameters: {
    docs: {
      description: {
        component:
          "The filled form of an application in Your application: its answers, the documents sent with it and the screenshots before and after the click; or that it is loading, or why it could not be read.",
      },
    },
  },
  args: {
    application: applicationIn("ready"),
    approval: approvalOf(),
    loadError: null,
    editable: true,
    saving: false,
    onSave: fn(() => Promise.resolve(true)),
    onDirtyChange: fn(),
  },
  beforeEach: () => {
    fakeFormShots();
  },
} satisfies Meta<typeof FilledForm>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Waiting for approval: the answers, the documents and the form before the click. */
export const WaitingForApproval: Story = {
  name: "Waiting for your approval",
};

/** A required question is left for the person, in amber above the answers. */
export const AQuestionLeft: Story = {
  name: "A question left for you",
  args: { approval: approvalOf(ONE_LEFT) },
};

/** Submitted: a record, with the page before and after the click. */
export const Submitted: Story = {
  name: "Submitted, before and after the click",
  args: {
    application: applicationIn("submitted"),
    editable: false,
    approval: approvalOf(undefined, {
      state: "submitted",
      screenshots: [
        {
          id: "shot-before",
          kind: "before",
          created_at: "2026-11-05T08:01:03Z",
        },
        { id: "shot-after", kind: "after", created_at: "2026-11-05T08:07:12Z" },
      ],
    }),
  },
};

/** No document and no screenshot: the answers alone. */
export const AnswersOnly: Story = {
  name: "Answers only",
  args: { approval: approvalOf(undefined, { documents: {}, screenshots: [] }) },
};

/** The filled form is being read. */
export const Loading: Story = {
  name: "Loading",
  args: { approval: undefined },
};

/** The filled form could not be read, and why. */
export const LoadFailed: Story = {
  name: "Could not be loaded",
  args: {
    approval: undefined,
    loadError: new ApiError("http", "Service unavailable", {
      status: 503,
      body: { detail: "The server is busy. Try again in a minute." },
    }),
  },
};

/** On a phone, one column and the screenshots one under the other. */
export const OnAPhone: Story = {
  name: "On a phone",
  parameters: { phone: true },
  args: { approval: approvalOf(ONE_LEFT) },
};
