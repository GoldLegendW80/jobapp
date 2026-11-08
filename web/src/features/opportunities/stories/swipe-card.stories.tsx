import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState, type ReactNode } from "react";
import { fn } from "storybook/test";

import { Button } from "@/components/ui/button";
import { DECK } from "@/design-system/fixtures/discover";
import { makeCard } from "@/test/deck";

import type { Card } from "../api/opportunities-api";
import type { Swipe } from "../api/deck/use-deck";
import { DeckScene } from "../components/deck/deck-scene";
import { DeckStack, type Toward } from "../components/deck/deck-stack";
import { SwipeCard } from "../components/deck/swipe-card";
import { createDeckSignals } from "../hooks/deck/use-deck-signals";

const TOP: Card = DECK[0] ?? makeCard(1);

const meta = {
  title: "Pages/Discover/Swipe card",
  component: SwipeCard,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "One posting of the deck as a card: the fit as the hero figure, the title, company, place, contract and pay, what matches and what lacks, the source and More. On top it follows a drag with a tilt and its Like or Pass stamp; Discover stacks it with the next ones (Card stack below).",
      },
    },
  },
  args: {
    card: TOP,
    role: "top",
    motion: true,
    interactive: true,
    signals: createDeckSignals(),
    onSwipe: fn(),
    onMore: fn(),
  },
  decorators: [
    (Story) => (
      <Frame>
        <Story />
      </Frame>
    ),
  ],
} satisfies Meta<typeof SwipeCard>;

export default meta;
type Story = StoryObj<typeof meta>;

function Frame({
  children,
  phone = false,
}: {
  children: ReactNode;
  phone?: boolean;
}) {
  return (
    <div className={phone ? "max-w-sm" : "max-w-[26rem]"}>
      <div className="relative h-[36rem]">{children}</div>
    </div>
  );
}

/** A strong fit: the ring full and green, the skills that match, the one gap. Drag it: it springs back. */
export const AStrongFit: Story = {};

/** A fair fit, its reason written as a sentence. */
export const AFairFitWithItsReason: Story = {
  args: {
    card: makeCard(4, {
      fit: 61,
      pay: "450 EUR a day",
      contract_type: "Freelance",
      work_mode: "remote",
      location: "",
      reason: {
        matches: [],
        gap: null,
        text: "Strong data modelling, but the role asks for on call duty on weekends.",
      },
    }),
  },
};

/** No pay stated and no score yet: both said plainly. */
export const NoPayAndNoScoreYet: Story = {
  args: {
    card: makeCard(5, {
      fit: null,
      pay: null,
      source: "manual",
      url: null,
      reason: { matches: [], gap: null, text: null },
    }),
  },
};

/** Long title, company and place: they wrap and clamp inside the card. */
export const LongWords: Story = {
  args: {
    card: makeCard(6, {
      title:
        "Senior platform engineer for developer tools, distributed workflow orchestration and reliable research infrastructure",
      company:
        "Cedar Example Research Infrastructure and Distributed Developer Tools Cooperative",
      location:
        "Nantes, France, with collaboration across Paris, Amsterdam, Helsinki and distributed European research teams",
      pay: "70,000 to 90,000 EUR a year",
      reason: {
        matches: [
          "Python",
          "Kafka",
          "Terraform",
          "Go",
          "Observability",
          "PostgreSQL",
          "Airflow",
        ],
        gap: "Kubernetes production capacity planning",
        text: null,
      },
    }),
  },
};

/** On a phone width. */
export const OnAPhone: Story = {
  decorators: [
    (Story) => (
      <Frame phone>
        <Story />
      </Frame>
    ),
  ],
};

/** The card stack as Discover draws it: drag the top card or use the buttons; Undo brings the last one back from its side. Nothing is sent. */
export const CardStack: Story = {
  decorators: [],
  render: function Stack() {
    const [signals] = useState(createDeckSignals);
    const [cards, setCards] = useState<readonly Card[]>(DECK);
    const [fling, setFling] = useState<Toward | null>(null);
    const [returning, setReturning] = useState<Toward | null>(null);
    const [gone, setGone] = useState<{ card: Card; kind: Swipe }[]>([]);
    const swipe = (kind: Swipe) => {
      const top = cards[0];
      if (!top) return;
      setFling({ id: top.id, kind });
      setCards((now) => now.slice(1));
      setGone((now) => [...now, { card: top, kind }]);
      signals.burst(kind);
    };
    const undo = () => {
      const last = gone[gone.length - 1];
      if (!last) return;
      setFling(null);
      setReturning({ id: last.card.id, kind: last.kind });
      setGone((now) => now.slice(0, -1));
      setCards((now) => [last.card, ...now]);
    };
    return (
      <div className="flex max-w-[26rem] flex-col gap-4">
        <DeckStack
          cards={cards}
          motion
          busy={false}
          fling={fling}
          returning={returning}
          focusTitle={false}
          signals={signals}
          onSwipe={swipe}
          onMore={fn()}
          className="h-[36rem]"
        >
          <DeckScene
            scene="Reaction"
            motion
            signals={signals}
            className="absolute inset-x-0 top-0 bottom-8 z-40"
          />
        </DeckStack>
        <div className="flex justify-center gap-2">
          <Button
            variant="outline"
            onClick={() => {
              swipe("pass");
            }}
            disabled={cards.length === 0}
          >
            Pass
          </Button>
          <Button variant="outline" onClick={undo} disabled={gone.length === 0}>
            Undo last
          </Button>
          <Button
            onClick={() => {
              swipe("like");
            }}
            disabled={cards.length === 0}
          >
            Like
          </Button>
        </div>
      </div>
    );
  },
};

/** Its page may say it is closed (the last check): the card says so before the Like. */
export const MayBeClosed: Story = {
  name: "May be closed",
  args: {
    card: makeCard(5, {
      liveness: {
        verdict: "may_be_closed",
        reason: "page_hints",
        sentence: "Job expired",
        checked_at: "2026-11-07T09:30:00Z",
      },
    }),
  },
};
