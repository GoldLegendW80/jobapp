import type { Meta, StoryObj } from "@storybook/react-vite";
import { HttpResponse } from "msw";
import { expect, userEvent, within } from "storybook/test";

import { AppAt } from "@/design-system/app-at";
import {
  fakePostingDetails,
  LONG_TITLE_POSTING,
  NO_PAY_NO_FIT_POSTING,
} from "@/design-system/fixtures/posting-details";
import { POSTINGS } from "@/design-system/fixtures/postings";
import { member, fakeSession } from "@/test/auth";
import { fakeJobsApi } from "@/test/jobs";

const meta = {
  title: "Pages/Jobs/Jobs",
  parameters: {
    layout: "fullscreen",
    page: true,
    docs: {
      description: {
        component:
          "The postings found for the person, scored against their profile. Route /jobs, the first page after signing in. From 1024px wide the list and the selected posting sit side by side, each scrolling on its own; the selection is in the address (`/jobs?job=<id>`), the first posting when none. Below, the list alone, a tap opening the posting's page.",
      },
    },
  },
  // The pane's "More about this posting" keeps its open state in local storage: start folded.
  beforeEach: () => {
    window.localStorage.removeItem("advanced:posting-more");
  },
  render: () => <AppAt path="/jobs" />,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** The postings of every story but the empty ones, and their pages. */
function postings(rows = POSTINGS) {
  fakeSession(member);
  fakeJobsApi(rows);
  fakePostingDetails(rows);
}

/** As a job seeker, I want to see every posting found for me with its fit, status and pay, so that I can pick which ones to act on. */
export const BrowsePostings: Story = {
  beforeEach: () => {
    postings();
  },
};

/** As a job seeker, I want the posting I pick in the list to open beside it, so that I can read it without losing my place. */
export const SelectAPosting: Story = {
  beforeEach: () => {
    postings();
  },
  play: async ({ canvasElement }) => {
    const page = within(canvasElement);
    const list = await page.findByRole("list", { name: "Postings" });
    const item = (await within(list).findByText("Atelier Brume")).closest("li");
    if (item) await userEvent.click(item);
    await expect(
      await page.findByRole("heading", {
        level: 2,
        name: POSTINGS[2]?.title,
      }),
    ).toBeVisible();
  },
};

/** As a job seeker, I want the selected posting kept in the address, so that a reload, back or a shared link shows it again. */
export const OpenALinkToAPosting: Story = {
  render: () => <AppAt path={`/jobs?job=${POSTINGS[3]?.id ?? ""}`} />,
  beforeEach: () => {
    postings();
  },
};

/** As a job seeker, I want to narrow the list by status, type, company, fit and dates in one panel, so that the list holds only what I want to see. */
export const FilterThePostings: Story = {
  render: () => (
    <AppAt path="/jobs?status=found&status=shortlisted&fit_min=40" />
  ),
  beforeEach: () => {
    postings();
  },
  play: async ({ canvasElement }) => {
    const page = within(canvasElement);
    await userEvent.click(
      await page.findByRole("button", { name: /^Filters/ }),
    );
  },
};

/** As a job seeker, I want to tick several postings and act on them at once, so that a morning of sorting takes a few clicks. */
export const ActOnASelection: Story = {
  beforeEach: () => {
    postings();
  },
  play: async ({ canvasElement }) => {
    const page = within(canvasElement);
    for (const company of ["Lumen Freight", "Kestrel Health"])
      await userEvent.click(
        await page.findByRole("checkbox", {
          name: new RegExp(` at ${company}$`),
        }),
      );
  },
};

/** As a job seeker, I want a long title to stay readable in the list and in full beside it, so that two similar postings never look the same. */
export const ReadALongTitle: Story = {
  beforeEach: () => {
    postings([LONG_TITLE_POSTING, ...POSTINGS]);
  },
};

/** As a job seeker, I want a posting without pay or score to say so plainly, so that I never mistake a gap for a figure. */
export const SeeAPostingWithoutPayOrFit: Story = {
  beforeEach: () => {
    postings([NO_PAY_NO_FIT_POSTING, ...POSTINGS]);
  },
};

/** As a job seeker, I want to know when my filters hide every posting, so that I can clear them in one click. */
export const NoPostingMatches: Story = {
  render: () => <AppAt path="/jobs?q=astronaut" />,
  beforeEach: () => {
    postings();
  },
};

/** As a job seeker, I want the empty list to tell me how to find postings, so that I am never stuck on a blank page. */
export const NoPostingsYet: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeJobsApi([]);
  },
};

/** As a job seeker, I want placeholders while the postings load, so that I know the page is working. */
export const WaitForThePostings: Story = {
  beforeEach: () => {
    fakeSession(member);
    // The api gives up at 15 seconds: the postings come after 10.
    fakeJobsApi(POSTINGS, { latencyMs: 10_000 });
    fakePostingDetails();
  },
};

/** As a job seeker, I want to know when the list could not be loaded, so that I can try again instead of thinking I have no postings. */
export const ListUnavailable: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeJobsApi([], {
      respond: () =>
        HttpResponse.json({ detail: "Server error" }, { status: 500 }),
    });
  },
};

/** As a job seeker, I want the list alone on my phone, a tap opening the posting, so that each card is easy to read and to tap. */
export const BrowseOnMyPhone: Story = {
  globals: { viewport: { value: "mobile2", isRotated: false } },
  beforeEach: () => {
    postings();
  },
};
