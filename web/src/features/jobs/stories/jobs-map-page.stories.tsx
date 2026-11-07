import type { Meta, StoryObj } from "@storybook/react-vite";
import { HttpResponse } from "msw";
import { userEvent, within } from "storybook/test";

import { AppAt } from "@/design-system/app-at";
import { fakePlaces } from "@/design-system/fixtures/places";
import {
  LONG_TITLE_POSTING,
  NO_PAY_NO_FIT_POSTING,
} from "@/design-system/fixtures/posting-details";
import { POSTINGS } from "@/design-system/fixtures/postings";
import { member, fakeSession } from "@/test/auth";
import { fakeJobsApi } from "@/test/jobs";

const meta = {
  title: "Pages/Jobs/Map",
  parameters: {
    layout: "fullscreen",
    page: true,
    docs: {
      description: {
        component:
          "The Map view of Jobs: the postings of the current filters on a world map, a pin per city or country their location names, the places listed beside it, and the locations no place matches. Route /jobs/map, under the same header, tabs and toolbar as the list, with the same filters in the address.",
      },
    },
  },
  render: () => <AppAt path="/jobs/map" />,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** The map of `rows`, and the list answer the toolbar counts from. */
function postings(rows = POSTINGS) {
  fakeSession(member);
  fakeJobsApi(rows);
  fakePlaces(rows);
}

/** As a job seeker, I want my postings pinned on a map, so that I see at a glance where the work is. */
export const SeeWhereThePostingsAre: Story = {
  beforeEach: () => {
    postings();
  },
};

/** As a job seeker, I want to pick a city and tick its postings, so that I can act on every posting in one place at once. */
export const PickAPlace: Story = {
  beforeEach: () => {
    postings();
  },
  play: async ({ canvasElement }) => {
    const page = within(canvasElement);
    const map = await page.findByRole("group", { name: "World map" });
    await userEvent.click(
      await within(map).findByRole("button", { name: /^Bordeaux: / }),
    );
    await userEvent.click(
      await page.findByRole("checkbox", {
        name: /^Select Product analyst at /,
      }),
    );
  },
};

/** As a job seeker, I want the map to follow the list's filters, so that switching views keeps what I narrowed. */
export const SeeOnlyTheFilteredPostings: Story = {
  render: () => <AppAt path="/jobs/map?type=CDI" />,
  beforeEach: () => {
    fakeSession(member);
    fakeJobsApi(POSTINGS);
    fakePlaces(POSTINGS.filter((posting) => posting.contract_type === "CDI"));
  },
};

/** As a job seeker, I want a long title and a posting without a score to read well in a place's list, so that each one stays recognisable. */
export const ReadALongTitleInAPlace: Story = {
  beforeEach: () => {
    postings([LONG_TITLE_POSTING, NO_PAY_NO_FIT_POSTING, ...POSTINGS]);
  },
  play: async ({ canvasElement }) => {
    const map = await within(canvasElement).findByRole("group", {
      name: "World map",
    });
    await userEvent.click(
      await within(map).findByRole("button", { name: /^Villeurbanne: / }),
    );
  },
};

/** As a job seeker, I want an empty map to say there is nothing to place yet and how to find postings, so that I am never left guessing. */
export const NothingToPlaceYet: Story = {
  beforeEach: () => {
    postings([]);
  },
};

/** As a job seeker, I want to know when the map could not be loaded, so that I can try again. */
export const MapUnavailable: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeJobsApi(POSTINGS);
    fakePlaces([], () =>
      HttpResponse.json({ detail: "Server error" }, { status: 500 }),
    );
  },
};

/** As a job seeker, I want the map above the list of places on my phone, so that I can still find postings by place. */
export const SeeTheMapOnMyPhone: Story = {
  globals: { viewport: { value: "mobile2", isRotated: false } },
  beforeEach: () => {
    postings();
  },
};
