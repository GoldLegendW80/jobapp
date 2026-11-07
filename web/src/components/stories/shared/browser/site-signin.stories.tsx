import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";
import { userEvent, within } from "storybook/test";

import { SiteSignIn } from "@/components/shared/browser/site-signin";
import { withApp } from "@/design-system/with-app";
import {
  browserHolds,
  fakeLiveScreens,
  lease,
  liveRoutes,
} from "@/design-system/fixtures/live-browser";
import { server } from "@/test/server";

const SITE = "https://members.example.org/jobs";
const SESSION = lease({ purpose: "signin", automation: false, max_s: 1800 });

function sites(state: "signed_in" | "expired" | null) {
  server.use(
    http.get("*/api/browser/sites", () =>
      HttpResponse.json({
        sites: state
          ? [
              {
                host: "members.example.org",
                state,
                signed_in_at: "2026-11-01T09:00:00Z",
              },
            ]
          : [],
      }),
    ),
  );
}

const meta = {
  title: "Shared/Browser/Website sign in",
  component: SiteSignIn,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "A job website's Sign in: the person's own browser opens on the website in the live view, they sign in themselves, and Done checks the login was kept. Beside each website of Find jobs and on Account's websites.",
      },
    },
  },
  args: { url: SITE },
  beforeEach: () => {
    liveRoutes();
    sites(null);
    browserHolds(SESSION);
    server.use(
      http.post("*/api/browser/sessions", () =>
        HttpResponse.json({ lease: SESSION, queue: null }),
      ),
    );
    return fakeLiveScreens();
  },
} satisfies Meta<typeof SiteSignIn>;

export default meta;
type Story = StoryObj<typeof meta>;

const signIn = async (canvasElement: HTMLElement) => {
  await userEvent.click(
    await within(canvasElement).findByRole("button", {
      name: /^Sign in to members\.example\.org/,
    }),
  );
};

/** Not signed in yet: one button. */
export const NotSignedIn: Story = { name: "Not signed in" };

/** In Find jobs, signing in is optional: only for a website that shows more to members. */
export const Optional: Story = {
  name: "Optional, in Find jobs",
  args: { optional: true },
};

/** The person signs in in their own browser, then clicks Done. */
export const SigningIn: Story = {
  name: "Signing in",
  play: async ({ canvasElement }) => {
    await signIn(canvasElement);
  },
};

/** Another lease holds the browser: the place in line. */
export const WaitingInLine: Story = {
  name: "Waiting in line",
  beforeEach: () => {
    browserHolds(null, { holder: "other" });
    server.use(
      http.post("*/api/browser/sessions", () =>
        HttpResponse.json({
          lease: null,
          queue: { purpose: "signin", position: 1, estimated_wait_s: 240 },
        }),
      ),
    );
  },
  play: async ({ canvasElement }) => {
    await signIn(canvasElement);
  },
};

/** Signed in: the searches read the website as a member. */
export const SignedIn: Story = {
  name: "Signed in",
  beforeEach: () => {
    sites("signed_in");
  },
};

/** The login ended: Sign in again, and why. */
export const LoginEnded: Story = {
  name: "Login ended",
  beforeEach: () => {
    sites("expired");
  },
};

/** On a phone the live view and Done take the width. */
export const OnAPhone: Story = {
  name: "On a phone",
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
  play: async ({ canvasElement }) => {
    await signIn(canvasElement);
  },
};
