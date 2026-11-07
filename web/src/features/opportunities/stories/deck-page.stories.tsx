import type { Meta, StoryObj } from "@storybook/react-vite";
import { delay, http, HttpResponse } from "msw";
import { userEvent, within } from "storybook/test";

import { AppAt } from "@/design-system/app-at";
import { DECK } from "@/design-system/fixtures/discover";
import { POSTING_DETAIL, POSTINGS } from "@/design-system/fixtures/postings";
import { fakeSession, member } from "@/test/auth";
import { fakeJobDetail, fakeJobsApi } from "@/test/jobs";
import { fakeProfile } from "@/test/profile";
import { server } from "@/test/server";

import { fakeDeck, refused } from "@/test/deck";

/** The first visit's swipe hint, as the page remembers it (deck-page.tsx). */
const HINT_KEY = "discover:swipe-hint";

function hintSeen(seen: boolean) {
  try {
    if (seen) window.localStorage.setItem(HINT_KEY, "1");
    else window.localStorage.removeItem(HINT_KEY);
  } catch {
    // Storage blocked: the hint shows, which only adds it to the picture.
  }
}

const meta = {
  title: "Pages/Discover/Discover",
  parameters: {
    layout: "fullscreen",
    page: true,
    docs: {
      description: {
        component:
          "The swipe deck, edge to edge under the header: one posting at a time on a stack of cards, best fit first. Like or Pass by a drag (the card tilts, shows its stamp, flies off past the threshold or springs back), by the buttons or by the arrow keys, and Undo last brings the card back the way it left. The mode a Like runs in stays in sight above the deck. Rive scenes play over it: a heart on a Like, an X on a Pass, the first visit's hint, the loading cards, the celebration of a finished deck. Route /discover.",
      },
    },
  },
  // Every story but the first visit's starts with the hint already seen.
  beforeEach: () => {
    hintSeen(true);
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

/** As a new user, I want the deck to show me how to swipe on my first visit, so that I know the card can be dragged. */
export const LearnToSwipeOnTheFirstVisit: Story = {
  beforeEach: () => {
    hintSeen(false);
    withPostingsAndSkills();
    fakeDeck(DECK);
  },
};

/** As a job seeker, I want a Like to answer me at once, so that I feel it was taken. */
export const LikeAndSeeTheHeart: Story = {
  beforeEach: () => {
    withPostingsAndSkills();
    fakeDeck(DECK);
  },
  play: async ({ canvasElement }) => {
    const page = within(canvasElement);
    await userEvent.click(await page.findByRole("button", { name: "Like" }));
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

/** As a job seeker, I want to see that my deck is on its way, so that I wait instead of thinking it is empty. The deck answers after 12 seconds. */
export const WaitForTheDeck: Story = {
  beforeEach: () => {
    withPostingsAndSkills();
    server.use(
      http.get("*/api/postings/deck", async () => {
        await delay(12_000);
        return HttpResponse.json({
          cards: DECK,
          next_cursor: null,
          left: DECK.length,
          mode: "review",
          min_fit: 70,
          rules_unreadable: false,
        });
      }),
    );
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

/** As a job seeker without skills in my profile, I want to know why nothing reaches my threshold, so that I add them. */
export const AddSkillsWhenEveryFitIsLow: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeJobsApi(POSTINGS);
    fakeProfile({});
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
