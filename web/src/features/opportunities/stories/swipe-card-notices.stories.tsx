import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { NOTICES } from "@/design-system/fixtures/posting-notices";
import { makeCard } from "@/test/deck";

import { SwipeCard } from "../components/deck/swipe-card";
import { createDeckSignals } from "../hooks/deck/use-deck-signals";

const meta = {
  title: "Pages/Discover/Swipe card before the click",
  component: SwipeCard,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "A deck card whose posting has something to know before the Like: the first two sentences under the place, contract and pay, the warnings first, the rest counted and read in More. A posting that closes within seven days comes first in the deck.",
      },
    },
  },
  args: {
    card: makeCard(1, { notices: NOTICES }),
    role: "top",
    motion: true,
    interactive: true,
    signals: createDeckSignals(),
    onSwipe: fn(),
    onMore: fn(),
  },
  decorators: [
    (Story) => (
      <div className="max-w-[26rem]">
        <div className="relative h-[36rem]">
          <Story />
        </div>
      </div>
    ),
  ],
} satisfies Meta<typeof SwipeCard>;

export default meta;
type Story = StoryObj<typeof meta>;

/** As a job seeker swiping, I want to see that a posting refuses the sponsorship I need and closes in four days, so that I Like or Pass knowing it. */
export const KnowBeforeILike: Story = {};

/** As a job seeker, I want a posting that closes this week said plainly on its card, so that I act on it first. */
export const ClosingThisWeek: Story = {
  args: {
    card: makeCard(2, {
      notices: [
        {
          kind: "closing",
          code: "closes_on",
          text: "This posting closes tomorrow, 11 November 2026.",
          quote: "Deadline: 11 November 2026.",
          level: "warning",
          job_id: null,
        },
      ],
    }),
  },
};
