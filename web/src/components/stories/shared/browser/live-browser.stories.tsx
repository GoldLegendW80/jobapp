import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";

import { LiveBrowser } from "@/components/shared/browser/live-browser";
import { withApp } from "@/design-system/with-app";
import {
  fakeLiveScreens,
  liveRoutes,
} from "@/design-system/fixtures/live-browser";

const meta = {
  title: "Shared/Browser/Live browser",
  component: LiveBrowser,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "The live view of the person's browser on the server, drawn by noVNC with a one use ticket: watched until Use the browser myself, then Let the assistant continue. Shown on Find jobs' progress, the Applications page, a job's Your application, a website search run, Account and every sign in.",
      },
    },
  },
  args: {
    label: "Welcome to the Jungle",
    detail: "www.welcometothejungle.com",
  },
  beforeEach: () => {
    liveRoutes();
    return fakeLiveScreens();
  },
} satisfies Meta<typeof LiveBrowser>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The assistant searches a job website; the person watches, the keyboard and the mouse are the assistant's. */
export const WatchingTheAssistant: Story = {
  name: "Watching the assistant",
};

/** The view asks for its ticket and opens its screen: it says so until the screen answers. */
export const Connecting: Story = {
  name: "Connecting",
  beforeEach: () => fakeLiveScreens({ connect: false }),
};

/** After Use the browser myself, the person types and clicks; the assistant waits for Let the assistant continue. */
export const UsingTheBrowserYourself: Story = {
  name: "Using the browser yourself",
  play: async ({ canvasElement }) => {
    const view = within(canvasElement);
    await userEvent.click(
      await view.findByRole("button", { name: "Use the browser myself" }),
    );
    await view.findByRole("button", { name: "Let the assistant continue" });
  },
};

/** The assistant asks for help on the page (a security check): the view says it needs the person. */
export const TheAssistantNeedsYou: Story = {
  name: "The assistant needs you",
  args: {
    needsYou: true,
    status:
      "The assistant needs your help on www.welcometothejungle.com. Choose Use the browser myself, do what the page asks, then Let the assistant continue.",
  },
};

/** The submit of an approved application: watched only, no Use the browser myself and no keyboard. */
export const WatchOnly: Story = {
  name: "Watch only, while a form is sent",
  args: {
    label: "Data engineer",
    detail: "Lumen Freight",
    watchOnly: true,
    status:
      "The assistant fills the form again with your approved answers, then submits it.",
  },
  beforeEach: () => fakeLiveScreens({ pages: ["form"] }),
};

/** The person's own session (a sign in): the keyboard and the mouse are theirs from the start. */
export const YourOwnSession: Story = {
  name: "Your own session",
  args: { label: undefined, detail: undefined, person: true },
};

/** The lease ended while the view was open: it says the view is closed and offers Reconnect. */
export const Ended: Story = {
  name: "Ended",
  beforeEach: () => fakeLiveScreens({ end: true }),
};

/** No ticket: the person holds no lease that started, so there is nothing to show. */
export const Unavailable: Story = {
  name: "Unavailable",
  beforeEach: () => {
    liveRoutes({ ticket: false });
  },
};

/** browserd turned the view away: three views of the browser are open already. */
export const TooManyViews: Story = {
  name: "Too many open views",
  beforeEach: () => fakeLiveScreens({ refuse: true }),
};

/** Without a label, as on a job page or a sign in: the screen, its status and its buttons. */
export const WithoutALabel: Story = {
  name: "Without a label",
  args: { label: undefined, detail: undefined },
};

/** On a phone the screen takes the width and the buttons wrap. */
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
