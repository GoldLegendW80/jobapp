import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";
import { userEvent, within } from "storybook/test";

import { AppAt } from "@/design-system/app-at";
import { DECK } from "@/design-system/fixtures/discover";
import { POSTING_DETAIL, POSTINGS } from "@/design-system/fixtures/postings";
import { fakeSession, member } from "@/test/auth";
import { fakeJobDetail, fakeJobsApi } from "@/test/jobs";
import { fakeProfile } from "@/test/profile";
import { server } from "@/test/server";

import { fakeDeck, refused } from "@/test/deck";

const meta = {
  title: "Pages/Discover/Discover",
  parameters: {
    layout: "fullscreen",
    page: true,
    docs: {
      description: {
        component:
          "The swipe deck: one posting at a time, Like or Pass by drag, by the buttons or by the arrow keys, and Undo last. A Like starts the CV, the letter and the application in the mode the person chose (Review first by default). Route /discover.",
      },
    },
  },
  render: () => <AppAt path="/discover" />,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** A person with postings and skills: an empty deck then means "under the threshold". */
const withPostingsAndSkills = () => {
  fakeSession(member);
  fakeJobsApi(POSTINGS);
  fakeProfile();
};

/** As a job seeker, I want to judge one posting at a time from its fit, pay and reason, so that I decide fast and in order of fit. */
export const LikeOrPassAPosting: Story = {
  beforeEach: () => {
    withPostingsAndSkills();
    fakeDeck(DECK);
  },
};

/** As a job seeker, I want to take back a Pass I made too fast, so that a good posting is not lost. */
export const UndoTheLastSwipe: Story = {
  beforeEach: () => {
    withPostingsAndSkills();
    fakeDeck(DECK);
  },
  play: async ({ canvasElement }) => {
    const page = within(canvasElement);
    await userEvent.click(await page.findByRole("button", { name: "Pass" }));
    await page.findByRole("button", { name: /Undo last/ });
  },
};

/** As a job seeker, I want to read the whole posting before I decide, so that a Like is never a guess. */
export const ReadThePostingInFull: Story = {
  beforeEach: () => {
    withPostingsAndSkills();
    fakeDeck(DECK);
    fakeJobDetail({
      ...POSTING_DETAIL,
      posting_text: POSTING_DETAIL.description,
    });
  },
  play: async ({ canvasElement }) => {
    const page = within(canvasElement);
    await userEvent.click(await page.findByRole("button", { name: /^More/ }));
  },
};

/** As a job seeker, I want to know why a Like was refused and what I can still do, so that I am never left wondering. */
export const SeeWhyALikeWasRefused: Story = {
  beforeEach: () => {
    withPostingsAndSkills();
    const deck = fakeDeck(DECK);
    const top = DECK[0];
    if (top)
      deck.likeAnswers.set(
        top.id,
        refused(
          "duplicate_role",
          "An application to the same role at this company is already under way.",
        ),
      );
  },
  play: async ({ canvasElement }) => {
    const page = within(canvasElement);
    await userEvent.click(await page.findByRole("button", { name: "Like" }));
    await page.findByRole("alert");
  },
};

/** As a job seeker in Full auto, I want the deck to say that a Like sends the application, so that I like only what I would send. */
export const LikeInFullAuto: Story = {
  beforeEach: () => {
    withPostingsAndSkills();
    fakeDeck(DECK, { mode: "full_auto" });
  },
};

/** As a job seeker who went through every card, I want to know that the rest is under my threshold, so that I can look for more or lower it. */
export const DeckDoneForToday: Story = {
  beforeEach: () => {
    withPostingsAndSkills();
    fakeDeck([]);
  },
};

/** As a new user, I want the empty deck to tell me how to find postings, so that I am never stuck on a blank page. */
export const NoPostingsYet: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeJobsApi([]);
    fakeProfile();
    fakeDeck([]);
  },
};

/** As a job seeker, I want to know when the deck could not be loaded, so that I try again instead of thinking nothing is left. */
export const DeckUnavailable: Story = {
  beforeEach: () => {
    withPostingsAndSkills();
    server.use(
      http.get("*/api/postings/deck", () =>
        HttpResponse.json({ detail: "Server error" }, { status: 500 }),
      ),
    );
  },
};

/** As a job seeker on my phone, I want Pass and Like under my thumb, so that I can go through the deck one handed. */
export const DiscoverOnAPhone: Story = {
  globals: { viewport: { value: "mobile2", isRotated: false } },
  beforeEach: () => {
    withPostingsAndSkills();
    fakeDeck(DECK);
  },
};
