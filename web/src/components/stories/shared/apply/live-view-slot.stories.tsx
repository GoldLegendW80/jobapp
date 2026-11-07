import type { Meta, StoryObj } from "@storybook/react-vite";

import { LiveViewSlot } from "@/components/shared/apply/live-view-slot";
import { withApp } from "@/design-system/with-app";
import {
  browserHolds,
  fakeLiveScreens,
  lease,
  liveRoutes,
} from "@/design-system/fixtures/live-browser";

const APPLICATION = "00000000-0000-4000-a000-000000000001";

const meta = {
  title: "Shared/Apply/Live view slot",
  component: LiveViewSlot,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "Where the live view of the person's browser goes while the assistant fills or sends an application: on a job's Your application, and named in the live views of the Applications page. Without this application's lease, a sentence says what comes.",
      },
    },
  },
  args: {
    idle: "The assistant is filling the form. You can watch it live here, then it sends it.",
    leaseOf: { application: APPLICATION },
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
    idle: "Waiting for your browser.",
    leaseOf: { application: APPLICATION },
    submit: true,
    status:
      "The assistant fills the form again with your approved answers, then submits it.",
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
