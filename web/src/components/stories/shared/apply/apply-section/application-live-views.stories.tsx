import type { Meta, StoryObj } from "@storybook/react-vite";

import { ApplicationLiveViews } from "@/components/shared/apply/apply-section/application-live-views";
import { APP_ID } from "@/test/apply";
import { applicationIn } from "@/design-system/fixtures/shared-apply";
import { withApp } from "@/design-system/with-app";
import {
  browserHolds,
  fakeLiveScreens,
  lease,
  liveRoutes,
} from "@/design-system/fixtures/live-browser";

const meta = {
  title: "Shared/Apply/Apply section/Application live views",
  component: ApplicationLiveViews,
  tags: ["autodocs"],
  decorators: [
    (Story) => (
      <div className="flex max-w-2xl flex-col gap-4">
        <Story />
      </div>
    ),
    withApp,
  ],
  parameters: {
    docs: {
      description: {
        component:
          "The live view of the person's browser in Your application while the app works: the wait before the fill, then the lease the api names for the application, whatever its state (a fill with Use the browser myself, the submit lease watch only). Without that lease it says what the application waits for, from its own needs.",
      },
    },
  },
  args: {
    application: applicationIn("filling"),
    phase: "filling",
    direct: false,
    agentFill: false,
    retrying: false,
    events: undefined,
    submitLease: null,
  },
} satisfies Meta<typeof ApplicationLiveViews>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Review first: the assistant fills the form, a screenshot appears when it is done. */
export const FillingReviewFirst: Story = {
  name: "Filling, then a screenshot",
};

/** One click Apply: watch it live, then it sends by itself. */
export const FillingOneClick: Story = {
  name: "Filling, then it sends",
  args: {
    application: applicationIn("filling", { mode: "direct" }),
    direct: true,
  },
};

/** Review first with the apply agent: watch it live, then check what it filled and confirm. */
export const FillingAgentReview: Story = {
  name: "Filling, then you check and confirm",
  args: { agentFill: true },
};

/** Waiting for a browser to start. */
export const WaitingForTheBrowser: Story = {
  name: "Waiting for the browser",
  args: { application: applicationIn("queued"), phase: "waiting" },
};

/** Waiting for the CV and letter before the browser starts. */
export const WaitingForTheDocuments: Story = {
  name: "Waiting for the documents",
  args: {
    application: applicationIn("queued", { needs: "documents:writing" }),
    phase: "waiting",
  },
};

/** The last attempt did not finish: the assistant tries again at the time the server set. */
export const TryingAgainLater: Story = {
  name: "Trying again later",
  args: {
    application: applicationIn("queued", { needs: "retry" }),
    phase: "waiting",
    retrying: true,
    events: [
      { at: "2026-11-05T08:00:00Z", kind: "created", data: {} },
      {
        at: "2026-11-05T08:04:12Z",
        kind: "retry",
        data: { due: "2026-11-05T08:20:00Z" },
      },
    ],
  },
};

/** Approved: the assistant fills the form again with the approved answers, then submits it. */
export const SubmittingAfterApproval: Story = {
  name: "Submitting after your approval",
  args: {
    application: applicationIn("approved"),
    phase: "submitting",
    submitLease: { application: APP_ID },
  },
};

/** One click Apply sends: the same answers, then the click. */
export const SendingOneClick: Story = {
  name: "Sending, One click Apply",
  args: {
    application: applicationIn("approved", { mode: "direct" }),
    phase: "submitting",
    direct: true,
    submitLease: { application: APP_ID },
  },
};

/** One click Apply's agent past its first send button: its own lease, with Use the browser myself. */
export const SendingOneClickAgent: Story = {
  name: "Sending, One click Apply's agent",
  args: {
    application: applicationIn("submitting", { mode: "direct" }),
    phase: "submitting",
    direct: true,
    submitLease: { application: APP_ID },
  },
  beforeEach: () => {
    liveRoutes();
    browserHolds(
      lease({ purpose: "apply_agent", application_id: APP_ID, max_s: 3600 }),
    );
    return fakeLiveScreens({ pages: ["form"] });
  },
};

/** An approved form waits for its turn: the app spaces out submissions to the same site. */
export const WaitingForItsTurn: Story = {
  name: "Waiting for its turn on the site",
  args: {
    application: applicationIn("approved", { needs: "host_pace" }),
    phase: "submitting",
    submitLease: { application: APP_ID },
  },
};

/** Held by Full auto: after the hold, the person can watch the fill again here. */
export const HeldByFullAuto: Story = {
  name: "Held by Full auto",
  args: {
    application: applicationIn("held", { mode: "full_auto" }),
    phase: "submitting",
    submitLease: { application: APP_ID },
  },
};

/** A rehearsal: the form is filled again and nothing is submitted. */
export const Rehearsal: Story = {
  name: "A rehearsal, nothing submitted",
  args: {
    application: applicationIn("approved", {
      mode: "full_auto",
      dry_run: true,
    }),
    phase: "submitting",
    submitLease: { application: APP_ID },
  },
};

/** On a phone. */
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
