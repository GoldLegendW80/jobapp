import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";

import { SweepBrowsers } from "@/components/shared/jobs/sweep-browsers";
import { withApp } from "@/design-system/with-app";
import {
  browserHolds,
  fakeLiveScreens,
  lease,
  liveRoutes,
} from "@/design-system/fixtures/live-browser";
import {
  runningSweep,
  websiteOfSweep,
} from "@/design-system/fixtures/sweep-progress";

const meta = {
  title: "Shared/Jobs/Websites in your browser",
  component: SweepBrowsers,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "Under Find jobs' progress on the Jobs list and map: Watch the browser opens the live view of the job website the assistant searches in the person's browser, named, with Use the browser myself. Nothing while only the sources without a browser are read.",
      },
    },
  },
  args: {
    sweep: runningSweep([
      websiteOfSweep("Welcome to the Jungle", "done"),
      websiteOfSweep("Free-Work", "running"),
      websiteOfSweep("Indeed", "pending"),
    ]),
  },
  beforeEach: () => {
    liveRoutes();
    browserHolds(lease());
    return fakeLiveScreens();
  },
} satisfies Meta<typeof SweepBrowsers>;

export default meta;
type Story = StoryObj<typeof meta>;

const watch = async (canvasElement: HTMLElement) => {
  await userEvent.click(
    await within(canvasElement).findByRole("button", {
      name: "Watch the browser",
    }),
  );
};

/** As the search starts: one button, nothing opened, so the assistant never waits for a person who is not there. */
export const BeforeWatching: Story = { name: "Before Watch the browser" };

/** The person watches the website being searched; Use the browser myself is one click away. */
export const WatchTheWebsiteSearched: Story = {
  name: "Watch the website searched",
  play: async ({ canvasElement }) => {
    await watch(canvasElement);
  },
};

/** The assistant asks for help on the page, a security check: the view says it needs the person. */
export const TheAssistantAsks: Story = {
  name: "The assistant asks for help",
  beforeEach: () => {
    browserHolds(
      lease({
        state: "waiting_person",
        wait_reason: "ask_person",
        wait_host: "www.free-work.com",
      }),
    );
  },
  play: async ({ canvasElement }) => {
    await watch(canvasElement);
  },
};

/** Another person's work holds the one browser: the website waits, with its place in line. */
export const WaitingForTheBrowser: Story = {
  name: "Waiting for the browser",
  beforeEach: () => {
    browserHolds(null, {
      holder: "other",
      queue: [{ purpose: "sweep", position: 1, estimated_wait_s: 150 }],
    });
  },
  play: async ({ canvasElement }) => {
    await watch(canvasElement);
  },
};

/** The browser does not answer: the view says so instead of a screen. */
export const BrowserUnavailable: Story = {
  name: "Browser unavailable",
  beforeEach: () => {
    browserHolds(null, { available: false });
  },
  play: async ({ canvasElement }) => {
    await watch(canvasElement);
  },
};

/** Two websites searched at once (owner, 2026-11-06): each window of the browser has its lease, which names its website, so each view shows its own window; a third website waits for a free one. */
export const TwoWebsitesAtOnce: Story = {
  name: "Two websites at once",
  args: {
    sweep: runningSweep([
      websiteOfSweep("Welcome to the Jungle", "running"),
      websiteOfSweep("Free-Work", "running"),
      websiteOfSweep("Indeed", "pending"),
    ]),
  },
  beforeEach: () => {
    browserHolds([
      lease({
        slot: 0,
        source_id: websiteOfSweep("Welcome to the Jungle", "running").source_id,
      }),
      lease({
        id: "8c1e2f3a-4b5c-4d6e-8f70-91a2b3c4d5e6",
        slot: 1,
        source_id: websiteOfSweep("Free-Work", "running").source_id,
      }),
    ]);
  },
  play: async ({ canvasElement }) => {
    await watch(canvasElement);
  },
};

/** Several websites at once from a server whose leases name no website: one view each, named, and each says it starts. */
export const SeveralWebsitesAtOnce: Story = {
  name: "Several websites at once",
  args: {
    sweep: runningSweep([
      websiteOfSweep("Welcome to the Jungle", "running"),
      websiteOfSweep("Free-Work", "running"),
      websiteOfSweep("Indeed", "running"),
    ]),
  },
  play: async ({ canvasElement }) => {
    await watch(canvasElement);
  },
};

/** Only the job feeds and boards run, which make no browser call: nothing to watch. */
export const NoWebsiteInTheBrowser: Story = {
  name: "No website in the browser yet",
  args: {
    sweep: runningSweep([
      { ...websiteOfSweep("Arbeitnow", "running"), mode: "api" },
      websiteOfSweep("Free-Work", "pending"),
    ]),
  },
};

/** On a phone the view takes the width. */
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
    await watch(canvasElement);
  },
};
