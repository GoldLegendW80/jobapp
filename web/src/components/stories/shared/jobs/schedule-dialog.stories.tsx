import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";
import { screen, userEvent } from "storybook/test";

import { ScheduleDialog } from "@/components/shared/jobs/schedule-dialog";
import { camilleProfile } from "@/design-system/fixtures/camille-profile";
import { REMOTE_SEARCH } from "@/design-system/fixtures/saved-searches";
import { WEEKDAYS_SCHEDULE } from "@/design-system/fixtures/jobs-actions";
import { phoneScreen } from "@/design-system/fixtures/phone-screen";
import { withApp } from "@/design-system/with-app";
import { fakeProfile } from "@/test/profile";
import { fakeSearches } from "@/test/searches";
import { emptySweeps, server } from "@/test/server";

const meta = {
  title: "Shared/Jobs/Schedule dialog",
  component: ScheduleDialog,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "The automatic search of one search: on or off, its days, one to four times, with when it runs next. The main search's (Automatic search in More actions on Jobs, or its row in Find jobs) holds the time zone every search shares; a saved search's (its row in Find jobs) names that time zone and where it is changed.",
      },
      story: { inline: false, height: "720px" },
    },
  },
  args: { state: emptySweeps.schedule, onClose: () => undefined },
  beforeEach: () => {
    fakeProfile(camilleProfile());
  },
} satisfies Meta<typeof ScheduleDialog>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Off, as for every new account: its days and times kept but muted. */
export const Off: Story = { name: "Off" };

/** On every weekday, morning and evening, with the next run. */
export const On: Story = {
  name: "On, every weekday",
  args: { state: WEEKDAYS_SCHEDULE },
};

/** On, but no job website ticked in Find jobs: nothing runs until one is. */
export const NoWebsite: Story = {
  name: "On without a website to search",
  args: { state: { ...WEEKDAYS_SCHEDULE, active: false } },
};

/** Before the schedule is known, the dialog starts from the defaults. */
export const Unknown: Story = {
  name: "The schedule still loading",
  args: { state: undefined },
};

/** Without a day there is no next run, and Save waits. */
export const NoDay: Story = {
  name: "No day picked",
  args: { state: { ...WEEKDAYS_SCHEDULE, days: ["monday"] } },
  play: async () => {
    await userEvent.click(
      await screen.findByRole("button", { name: "Monday" }),
    );
  },
};

/** Turning it on and saving closes the dialog with a toast. */
export const TurnOn: Story = {
  name: "Turn it on",
  play: async () => {
    await userEvent.click(await screen.findByLabelText("Search automatically"));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
  },
};

/** When saving fails, the dialog stays with what went wrong. */
export const SaveFails: Story = {
  name: "Not saved",
  beforeEach: () => {
    server.use(
      http.put("*/api/profile/search", () =>
        HttpResponse.json({ detail: "Server error" }, { status: 500 }),
      ),
    );
  },
  play: TurnOn.play,
};

/** On a phone the dialog takes the width and scrolls. */
export const OnAPhone: Story = {
  name: "On a phone",
  args: { state: WEEKDAYS_SCHEDULE },
  beforeEach: phoneScreen,
};

/** A saved search's schedule: the time zone is named, never offered, as every search shares it. */
export const SavedSearch: Story = {
  name: "The schedule of a saved search",
  args: { search: REMOTE_SEARCH },
  beforeEach: () => {
    fakeSearches({ saved: [REMOTE_SEARCH] });
  },
};

/** A saved search changed in another tab meanwhile: the dialog says so and keeps the choice. */
export const SavedSearchMoved: Story = {
  name: "A saved search changed in another tab",
  args: { search: { ...REMOTE_SEARCH, revision: 0 } },
  beforeEach: SavedSearch.beforeEach,
  play: async () => {
    await userEvent.click(await screen.findByRole("button", { name: "Save" }));
  },
};

/** A saved search's schedule on a phone: the same width as the main search's. */
export const SavedSearchOnAPhone: Story = {
  name: "The schedule of a saved search on a phone",
  args: { search: REMOTE_SEARCH },
  beforeEach: () => {
    fakeSearches({ saved: [REMOTE_SEARCH] });
    return phoneScreen();
  },
};
