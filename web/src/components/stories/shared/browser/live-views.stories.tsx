import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState, type ReactNode } from "react";

import { LiveBrowser } from "@/components/shared/browser/live-browser";
import {
  LiveViewPlaceholder,
  LiveViews,
} from "@/components/shared/browser/live-views";
import { withApp } from "@/design-system/with-app";
import {
  fakeLiveScreens,
  liveRoutes,
} from "@/design-system/fixtures/live-browser";

/** The views with their own open state, as Find jobs and the Applications page hold it. */
function Views({
  label = "Websites searched in your browser",
  open: initial = true,
  children,
}: {
  label?: string;
  open?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(initial);
  return (
    <LiveViews label={label} open={open} onOpenChange={setOpen}>
      {children}
    </LiveViews>
  );
}

const meta = {
  title: "Shared/Browser/Live views",
  component: LiveViews,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    layout: "padded",
    docs: {
      description: {
        component:
          "One or more live views of the person's browser behind Watch the browser, each named with its state: one at a readable width, several side by side on a wide screen, stacked on a phone. Find jobs' progress and the Applications page show it; it opens only on the person's click, since an open view makes the assistant wait for the person when it asks.",
      },
    },
  },
  args: {
    label: "Websites searched in your browser",
    open: true,
    onOpenChange: () => undefined,
    children: null,
  },
  beforeEach: () => {
    liveRoutes();
    return fakeLiveScreens();
  },
} satisfies Meta<typeof LiveViews>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Closed, as every page opens it: one button, and no view counts the person as watching. */
export const ClosedUntilWatched: Story = {
  name: "Closed until Watch the browser",
  render: () => (
    <Views open={false}>
      <LiveBrowser
        label="Welcome to the Jungle"
        detail="www.welcometothejungle.com"
      />
    </Views>
  ),
};

/** One website searched now, the case of every search today: one view at a readable width. */
export const OneView: Story = {
  name: "One website searched",
  render: () => (
    <Views>
      <LiveBrowser
        label="Welcome to the Jungle"
        detail="www.welcometothejungle.com"
      />
    </Views>
  ),
};

/** Several browsers at once, as when the server runs work side by side: each view its own label and state. */
export const SeveralAtOnce: Story = {
  name: "Several at once",
  render: () => (
    <Views label="Applications in your browser">
      <LiveBrowser key="1" label="Data engineer" detail="Lumen Freight" />
      <LiveBrowser
        key="2"
        label="Senior data engineer"
        detail="Example Robotics"
        watchOnly
        status="The assistant fills the form again with your approved answers, then submits it."
      />
      <LiveViewPlaceholder
        key="3"
        label="Analytics engineer"
        detail="Northwind Logistics"
        state="waiting"
      >
        Waiting for your browser. The app spaces out submissions to the same
        site, so this one may wait its turn.
      </LiveViewPlaceholder>
    </Views>
  ),
};

/** Every state a view can be in, side by side. */
export const EveryState: Story = {
  name: "Every state",
  beforeEach: () => fakeLiveScreens({ connect: false }),
  render: () => (
    <Views>
      <LiveBrowser
        key="connecting"
        label="Connecting"
        detail="free-work.example.org"
      />
      <LiveViewPlaceholder key="starting" label="Starting" state="starting">
        Your browser is starting.
      </LiveViewPlaceholder>
      <LiveViewPlaceholder key="waiting" label="Waiting" state="waiting">
        Your browser starts in about 3 minutes.
      </LiveViewPlaceholder>
      <LiveViewPlaceholder key="needs-you" label="Needs you" state="needs-you">
        The assistant waits for you to sign in to jobs.example.org. Sign in from
        this application&apos;s card.
      </LiveViewPlaceholder>
      <LiveViewPlaceholder key="ended" label="Ended" state="ended">
        The browser shows here while this website is searched.
      </LiveViewPlaceholder>
      <LiveViewPlaceholder
        key="unavailable"
        label="Unavailable"
        state="unavailable"
      >
        The browser is not available right now.
      </LiveViewPlaceholder>
    </Views>
  ),
};

/** On a phone the views stack, one under the other, each at the full width. */
export const OnAPhone: Story = {
  name: "On a phone",
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
  render: () => (
    <Views>
      <LiveBrowser
        key="1"
        label="Welcome to the Jungle"
        detail="www.welcometothejungle.com"
      />
      <LiveViewPlaceholder key="2" label="Free-Work" state="waiting">
        The browser is busy. This website&apos;s search starts when it is free.
      </LiveViewPlaceholder>
    </Views>
  ),
};
