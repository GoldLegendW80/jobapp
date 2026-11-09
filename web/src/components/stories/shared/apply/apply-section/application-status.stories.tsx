import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";

import { ApplicationStatus } from "@/components/shared/apply/apply-section/application-status";
import { inMinutes } from "@/design-system/fixtures/applications";
import { applicationIn } from "@/design-system/fixtures/shared-apply";
import { withApp } from "@/design-system/with-app";
import { reviewReason } from "@/features/apply/helpers/apply-state";

const meta = {
  title: "Shared/Apply/Apply section/Application status",
  component: ApplicationStatus,
  tags: ["autodocs"],
  decorators: [
    (Story) => (
      <div className="flex max-w-xl flex-col gap-4">
        <Story />
      </div>
    ),
    withApp,
  ],
  parameters: {
    docs: {
      description: {
        component:
          "Where the application stands, at the top of Your application: its status line, the page that settles what its documents wait for, why it waits for the person, the Full auto hold, a refresh that failed and the last refusal.",
      },
    },
  },
  args: {
    application: null,
    reason: null,
    holdUntil: null,
    holdOffset: 0,
    refreshFailed: false,
    notice: null,
  },
} satisfies Meta<typeof ApplicationStatus>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Nothing started on this posting yet. */
export const NoApplication: Story = {
  name: "No application yet",
};

/** Waiting for a browser to start. */
export const WaitingForABrowser: Story = {
  name: "Waiting for a browser",
  args: { application: applicationIn("queued") },
};

/** The CV and letter wait for a model credential: the link opens the account. */
export const WaitingForACredential: Story = {
  name: "Waiting for a model credential",
  args: {
    application: applicationIn("queued", { needs: "documents:credential" }),
  },
};

/** One click Apply writes the documents, then sends. */
export const WritingThenSending: Story = {
  name: "Writing the documents, then sending",
  args: {
    application: applicationIn("queued", {
      mode: "direct",
      needs: "documents:writing",
    }),
  },
};

/** Filled and waiting for the person's approval: nothing is sent before. */
export const WaitingForApproval: Story = {
  name: "Waiting for your approval",
  args: { application: applicationIn("ready") },
};

/** A form the app would have sent by itself waits for the person, and the page says why. */
export const WhyItWaitsForYou: Story = {
  name: "Why it waits for you",
  args: {
    application: applicationIn("ready", { error_code: "agent_text" }),
    reason: reviewReason(applicationIn("ready", { error_code: "agent_text" })),
  },
};

/** Full auto holds it so the person can cancel, with the countdown. */
export const HeldByFullAuto: Story = {
  name: "Held by Full auto",
  args: { application: applicationIn("held", { mode: "full_auto" }) },
  render: function Render(args) {
    const [until] = useState(() => inMinutes(9));
    return <ApplicationStatus {...args} holdUntil={until} />;
  },
};

/** Applied: the form was submitted. */
export const Applied: Story = {
  name: "Applied",
  args: {
    application: applicationIn("submitted", {
      submitted_at: "2026-11-05T08:07:13Z",
    }),
  },
};

/** The last attempt failed, and why; nothing was sent. */
export const Failed: Story = {
  name: "The last attempt failed",
  args: {
    application: applicationIn("failed", { error_code: "form_changed" }),
  },
};

/** A refresh failed: what shows may be out of date. */
export const RefreshFailed: Story = {
  name: "A refresh failed",
  args: { application: applicationIn("filling"), refreshFailed: true },
};

/** The api refused the last action: its sentence shows. */
export const ARefusal: Story = {
  name: "The last action was refused",
  args: {
    application: applicationIn("ready"),
    notice:
      "The form or your documents changed since you looked. Check the answers shown now and approve again.",
  },
};

/** A state this build does not know: nothing can be clicked. */
export const UnknownState: Story = {
  name: "A state this page does not know",
  args: { application: applicationIn("archived_by_admin") },
};
