import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { ConfirmDialog } from "@/components/shared/apply/confirm-dialog";
import { DECISIONS } from "@/features/apply/helpers/decisions";

const meta = {
  title: "Shared/Apply/Confirm dialog",
  component: ConfirmDialog,
  tags: ["autodocs"],
  parameters: {
    docs: {
      // Each story in its own frame on the docs page, so an open dialog covers only its own.
      story: { inline: false, height: "360px" },
      description: {
        component:
          "The second click of every decision on an application that cannot be taken back: Reject, Cancel, It was submitted, Try again. In Your application, the review queue and the Full auto hold.",
      },
    },
  },
  args: {
    ask: DECISIONS.reject,
    pending: false,
    onCancel: fn(),
    onConfirm: fn(),
  },
} satisfies Meta<typeof ConfirmDialog>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Reject a filled form: nothing is sent. */
export const RejectAFilledForm: Story = {
  name: "Reject a filled form",
};

/** Cancel an application that is not sent yet. */
export const CancelAnApplication: Story = {
  name: "Cancel an application",
  args: { ask: DECISIONS.cancel },
};

/** Cancel during a Full auto hold, in the hold's own words. */
export const CancelDuringTheHold: Story = {
  name: "Cancel during the hold",
  args: {
    ask: {
      title: "Cancel this application?",
      sentence:
        "The hold stops and nothing is sent. If the app already started submitting, it is too late and the page says so.",
      confirm: "Cancel the application",
    },
  },
};

/** The person saw the site's confirmation the app could not read: marking it submitted cannot be undone. */
export const MarkAsSubmitted: Story = {
  name: "Mark as submitted",
  args: { ask: DECISIONS.submitted },
};

/** Try again after a click the app could not confirm, in Review first: back to the approval. */
export const TryAgainReviewFirst: Story = {
  name: "Try again, fill and approve again",
  args: { ask: DECISIONS.retry },
};

/** Try again after One click Apply: the assistant applies and sends by itself again. */
export const TryAgainOneClick: Story = {
  name: "Try again, apply again",
  args: { ask: DECISIONS.retryAgent },
};

/** Try again after Review first with the apply agent: a new list to confirm, nothing sent before. */
export const TryAgainAgentReview: Story = {
  name: "Try again, fill it again",
  args: { ask: DECISIONS.retryReview },
};

/** After the confirm click, both buttons wait for the server. */
export const Waiting: Story = {
  name: "Waiting for the server",
  args: { ask: DECISIONS.cancel, pending: true },
};
