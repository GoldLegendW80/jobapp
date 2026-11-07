import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";
import { userEvent, within } from "storybook/test";

import { HandoffBanner } from "@/components/shared/browser/handoff-banner";
import { withApp } from "@/design-system/with-app";
import {
  browserHolds,
  fakeLiveScreens,
  lease,
  liveRoutes,
} from "@/design-system/fixtures/live-browser";
import { server } from "@/test/server";

/** The person's fill, waiting for them to sign in on the form's website. */
const signInWall = (minutes: number) =>
  lease({
    purpose: "apply_fill",
    state: "waiting_person",
    wait_reason: "signin",
    wait_host: "jobs.example.org",
    wait_deadline: "2026-11-06T09:30:00Z",
    wait_minutes_left: minutes,
    max_s: 900,
  });

const meta = {
  title: "Shared/Browser/Sign in hand off",
  component: HandoffBanner,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "An assistant met a sign in wall while the person watched and waits for them: the website, the minutes left, Open the live view, I'm signed in and Cancel. On a job's Your application, its card on the Applications page, and Account.",
      },
    },
  },
  beforeEach: () => {
    liveRoutes();
    browserHolds(signInWall(12));
    return fakeLiveScreens({ pages: ["form"] });
  },
} satisfies Meta<typeof HandoffBanner>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The banner waits for the person: the live view opens on their click. */
export const WaitingForYou: Story = { name: "Waiting for you" };

/** The live view open, the keyboard and the mouse the person's, to sign in. */
export const SigningIn: Story = {
  name: "Signing in, the live view open",
  args: { liveOpen: true },
};

/** One minute left before the assistant gives up waiting. */
export const OneMinuteLeft: Story = {
  name: "One minute left",
  beforeEach: () => {
    browserHolds(signInWall(1));
  },
};

/** I'm signed in did not go through: the reason, and the buttons stay. */
export const NotSignedIn: Story = {
  name: "I'm signed in refused",
  beforeEach: () => {
    server.use(
      http.post("*/api/browser/handoff/done", () =>
        HttpResponse.json(
          { detail: "The browser is not available right now." },
          { status: 503 },
        ),
      ),
    );
  },
  play: async ({ canvasElement }) => {
    await userEvent.click(
      await within(canvasElement).findByRole("button", {
        name: "I'm signed in",
      }),
    );
  },
};

/** Nothing waits for a sign in: the banner shows nothing. */
export const NothingWaits: Story = {
  name: "Nothing waits",
  beforeEach: () => {
    browserHolds(null);
  },
};

/** On a phone the buttons wrap under the sentence. */
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
