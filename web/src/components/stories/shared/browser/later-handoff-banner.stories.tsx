import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";
import { userEvent, within } from "storybook/test";

import { LaterHandoffBanner } from "@/components/shared/browser/later-handoff-banner";
import { withApp } from "@/design-system/with-app";
import {
  browserHolds,
  fakeLiveScreens,
  lease,
  liveRoutes,
} from "@/design-system/fixtures/live-browser";
import { server } from "@/test/server";

const SESSION = lease({ purpose: "signin", automation: false, max_s: 1800 });

const meta = {
  title: "Shared/Browser/Sign in later",
  component: LaterHandoffBanner,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "Work that stopped at a sign in wall waits for the person: Open the live view to sign in starts their own session at the form's address, then I'm signed in queues the work again. On a job's Needs you and the application's card on the Applications page; under the assistant's question, its button names the step (solve the check, enter the code) and is outline beside the question's own I did it, go on.",
      },
    },
  },
  args: {
    module: "application",
    refId: "00000000-0000-4000-a000-000000000001",
    address: "https://jobs.example.org/lumen-freight/apply",
  },
  beforeEach: () => {
    liveRoutes();
    browserHolds(SESSION);
    server.use(
      http.post("*/api/browser/handoff/later", () =>
        HttpResponse.json({ lease: SESSION, queue: null }),
      ),
    );
    return fakeLiveScreens({ pages: ["form"] });
  },
} satisfies Meta<typeof LaterHandoffBanner>;

export default meta;
type Story = StoryObj<typeof meta>;

const open = async (canvasElement: HTMLElement) => {
  await userEvent.click(
    await within(canvasElement).findByRole("button", {
      name: "Open the live view to sign in",
    }),
  );
};

/** The work waits; nothing opens until the person asks. */
export const NeedsYou: Story = { name: "Needs you" };

/** The person's own session at the form's address: they sign in, then click I'm signed in. */
export const SigningIn: Story = {
  name: "Signing in",
  play: async ({ canvasElement }) => {
    await open(canvasElement);
  },
};

/** Another lease holds the browser: the person opens the live view again when it is free. */
export const BrowserBusy: Story = {
  name: "Browser busy",
  beforeEach: () => {
    server.use(
      http.post("*/api/browser/handoff/later", () =>
        HttpResponse.json({ lease: null, queue: { position: 1 } }),
      ),
    );
  },
  play: async ({ canvasElement }) => {
    await open(canvasElement);
  },
};

/** Under the job page's Needs you alert, which names the site already: no heading of its own. */
export const WithoutHeading: Story = {
  name: "Without its heading",
  args: { heading: false },
};

/** Under the assistant's question, which has its own primary action: the button names the step and is outline. */
export const ACheckUnderAQuestion: Story = {
  name: "A check, under the assistant's question",
  args: { heading: false, step: "check", secondary: true },
};

/** On a phone the banner takes the width. */
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
