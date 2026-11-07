import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";

import { AppAt } from "@/design-system/app-at";
import { fakeSession, member } from "@/test/auth";
import { server } from "@/test/server";

const meta = {
  title: "Pages/Public/Home",
  parameters: {
    layout: "fullscreen",
    page: true,
    docs: {
      description: {
        component:
          "What the app is, for a visitor without a session, and the way to sign in. Route /; a signed in visitor goes straight to Jobs.",
      },
    },
  },
  render: () => <AppAt path="/" />,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** As an invited friend, I want to read in two sentences what the app does and how to get in, so that I know I am in the right place before I sign in. */
export const ReadWhatTheAppIs: Story = {
  beforeEach: () => {
    fakeSession(null);
  },
};

/** As a job seeker already signed in, I want the home address to open my jobs, so that I never stop on a page meant for visitors. */
export const SignedInGoesToJobs: Story = {
  beforeEach: () => {
    fakeSession(member);
  },
};

/** As a visitor, I want the home page to show even when the server is down, so that I can still read what the app is and find the privacy page. */
export const ServerDown: Story = {
  beforeEach: () => {
    server.use(
      http.get("*/api/auth/me", () =>
        HttpResponse.json({ detail: "Service unavailable" }, { status: 503 }),
      ),
    );
  },
};

/** As an invited friend on my phone, I want the name, the sentences and Sign in in one column, so that I can sign in from the link I was sent. */
export const OnAPhone: Story = {
  globals: { viewport: { value: "mobile2", isRotated: false } },
  beforeEach: () => {
    fakeSession(null);
  },
};
