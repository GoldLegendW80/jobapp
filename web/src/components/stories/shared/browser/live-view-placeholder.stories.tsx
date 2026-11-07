import type { Meta, StoryObj } from "@storybook/react-vite";

import { LiveViewPlaceholder } from "@/components/shared/browser/live-views";

const meta = {
  title: "Shared/Browser/Live view placeholder",
  component: LiveViewPlaceholder,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "A named live view without its screen, at the screen's size: the browser starts, waits its turn, needs the person elsewhere or cannot be shown. In the live views of Find jobs' progress and of the Applications page.",
      },
    },
  },
  args: {
    label: "Welcome to the Jungle",
    detail: "www.welcometothejungle.com",
    state: "starting",
    children: "Your browser is starting.",
  },
  decorators: [
    (Story) => (
      <div className="max-w-xl">
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof LiveViewPlaceholder>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The lease is starting: Chromium opens on the person's profile. */
export const Starting: Story = { name: "Starting" };

/** Another lease holds the one browser; this work waits in line. */
export const WaitingItsTurn: Story = {
  name: "Waiting its turn",
  args: {
    state: "waiting",
    children: "Your browser starts in about 3 minutes.",
  },
};

/** The assistant waits for the person to sign in, from the application's card. */
export const NeedsYou: Story = {
  name: "Needs you",
  args: {
    label: "Data engineer",
    detail: "Lumen Freight",
    state: "needs-you",
    children:
      "The assistant waits for you to sign in to jobs.example.org. Sign in from this application's card.",
  },
};

/** The browser does not answer. */
export const Unavailable: Story = {
  name: "Unavailable",
  args: {
    state: "unavailable",
    children: "The browser is not available right now.",
  },
};

/** A long name is cut on one line; the state stays in view. */
export const LongName: Story = {
  name: "Long name",
  args: {
    label:
      "Senior data platform engineer, streaming and batch pipelines for rail freight planning",
    detail: "Contoso Rail Freight Planning and Logistics Services",
  },
};
