import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";

import { SweepProgress } from "@/components/shared/jobs/find-jobs";
import type { Sweep } from "@/features/jobs/api/sweeps-api";
import type { SweepRunner } from "@/features/jobs/api/use-sweep-runner";
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

/** The progress of `sweep`, as the Jobs page's runner holds it while it runs. */
function Progress({ sweep }: { sweep: Sweep }) {
  return <SweepProgress runner={{ running: sweep } as SweepRunner} />;
}

const meta = {
  title: "Shared/Jobs/Search progress",
  component: Progress,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "The line under the Jobs header while Find jobs runs, on the list and the map: how many sources are searched, the one read now, the new postings so far, and the browser of the website searched behind Watch the browser.",
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
} satisfies Meta<typeof Progress>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A website is searched in the person's browser: the line names it, Watch the browser shows it. */
export const SearchingAWebsite: Story = { name: "Searching a website" };

/** The person opened the browser: the search's live view under the line. */
export const WatchingTheSearch: Story = {
  name: "Watching the search",
  play: async ({ canvasElement }) => {
    await userEvent.click(
      await within(canvasElement).findByRole("button", {
        name: "Watch the browser",
      }),
    );
  },
};

/** The job feeds read side by side first: no browser yet, so nothing to watch. */
export const ReadingFeeds: Story = {
  name: "Reading job feeds first",
  args: {
    sweep: runningSweep([
      { ...websiteOfSweep("Arbeitnow", "running"), mode: "api" },
      websiteOfSweep("Welcome to the Jungle", "pending"),
    ]),
  },
};

/** The worker has not picked the search up yet. */
export const WaitingForTheWorker: Story = {
  name: "Waiting for the worker",
  args: { sweep: runningSweep([]) },
};

/** On a phone the line wraps and the view takes the width. */
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
    await userEvent.click(
      await within(canvasElement).findByRole("button", {
        name: "Watch the browser",
      }),
    );
  },
};
