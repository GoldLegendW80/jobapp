import type { Meta, StoryObj } from "@storybook/react-vite";

import { LiveViewSlot } from "@/components/shared/apply/live-view-slot";
import { withApp } from "@/design-system/with-app";
import { applicationIn } from "@/design-system/fixtures/shared-apply";
import { liveStatus } from "@/features/apply/helpers/live-view";
import {
  browserHolds,
  fakeLiveScreens,
  lease,
  liveRoutes,
} from "@/design-system/fixtures/live-browser";

const APPLICATION = "00000000-0000-4000-a000-000000000001";

/** The status line of `run`'s lease, as the job page and the Applications page give it. */
const statusOf =
  (...run: Parameters<typeof applicationIn>) =>
  (purpose: string) =>
    liveStatus(purpose, applicationIn(...run));

const meta = {
  title: "Shared/Apply/Live view slot",
  component: LiveViewSlot,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "Where the live view of the person's browser goes while the assistant fills or sends an application: on a job's Your application, and named in the live views of the Applications page. It shows the lease the api names for the application, whatever the application's state: a fill with Use the browser myself, the submit lease watch only. Without that lease, a sentence says what the application waits for.",
      },
    },
  },
  args: {
    idle: "The assistant is filling the form. You can watch it live here, then it sends it.",
    leaseOf: { application: APPLICATION },
    status: statusOf("filling", { mode: "direct" }),
  },
  beforeEach: () => {
    liveRoutes();
    browserHolds(
      lease({
        purpose: "apply_agent",
        application_id: APPLICATION,
        max_s: 3600,
      }),
    );
    return fakeLiveScreens({ pages: ["form"] });
  },
} satisfies Meta<typeof LiveViewSlot>;

export default meta;
type Story = StoryObj<typeof meta>;

/** One click Apply's agent fills the form: watched, with Use the browser myself. */
export const TheAssistantFills: Story = {
  name: "The assistant fills the form",
};

/** One click Apply's agent past its first send button: still its lease, still Use the browser myself. */
export const TheAssistantSends: Story = {
  name: "The assistant sends the form",
  args: {
    idle: "Sending the form. Its result shows here once the app has it.",
    status: statusOf("submitting", { mode: "direct" }),
    namedOnly: true,
  },
};

/** The fill has not started: the sentence in its place. */
export const BeforeTheFill: Story = {
  name: "Before the fill",
  beforeEach: () => {
    browserHolds(null);
  },
};

/** The approved form is sent on the submit lease: watched only. */
export const TheFormIsSent: Story = {
  name: "The form is sent, watched only",
  args: {
    idle: "Your browser starts soon. Then you can watch the assistant send the form here.",
    status: statusOf("approved"),
    namedOnly: true,
  },
  beforeEach: () => {
    browserHolds(
      lease({
        purpose: "apply_submit",
        application_id: APPLICATION,
        max_s: 900,
      }),
    );
  },
};

/** An approved form waits for its turn on the site: only a pace says so. */
export const WaitingForItsTurn: Story = {
  name: "Waiting for its turn on the site",
  args: {
    idle: "Waiting for its turn. The app spaces out submissions to the same site, so this one waits a little.",
    namedOnly: true,
  },
  beforeEach: () => {
    browserHolds(null);
  },
};

/** A captcha on the page: the keyboard and the mouse are the person's for it. */
export const ACaptchaWaitsForYou: Story = {
  name: "A captcha waits for you",
  beforeEach: () => {
    browserHolds(
      lease({
        purpose: "apply_fill",
        application_id: APPLICATION,
        state: "waiting_person",
        wait_reason: "captcha",
        wait_host: "jobs.example.org",
        max_s: 900,
      }),
    );
  },
};

/** A sign in wall: the hand off with its live view open. */
export const SignInWall: Story = {
  name: "A sign in wall",
  beforeEach: () => {
    browserHolds(
      lease({
        purpose: "apply_fill",
        application_id: APPLICATION,
        state: "waiting_person",
        wait_reason: "signin",
        wait_host: "jobs.example.org",
        wait_deadline: "2026-11-06T09:30:00Z",
        wait_minutes_left: 12,
        max_s: 900,
      }),
    );
  },
};

/** Named, as in the live views of the Applications page. */
export const NamedInAGrid: Story = {
  name: "Named, on the Applications page",
  args: { label: "Data engineer", detail: "Lumen Freight" },
  decorators: [
    (Story) => (
      <div className="max-w-3xl">
        <Story />
      </div>
    ),
  ],
};

/** On a phone the view and its buttons take the width. */
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
