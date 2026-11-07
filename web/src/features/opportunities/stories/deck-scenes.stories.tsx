import type { Meta, StoryObj } from "@storybook/react-vite";
import { Layers, MoveHorizontal, PartyPopper } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";

import { DeckScene } from "../components/deck/deck-scene";
import { createDeckSignals } from "../hooks/deck/use-deck-signals";

const meta = {
  title: "Pages/Discover/Rive scenes",
  component: DeckScene,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The Rive scenes of Discover, one artboard each in features/opportunities/assets/discover.riv (source: web/rive/discover/, RML built with the Rive CLI). Each plays the state machine of its name; the runtime loads lazily once the browser is idle, never with reduced motion, and the fallback stays when it cannot load.",
      },
    },
  },
  args: { scene: "Loading", motion: true },
} satisfies Meta<typeof DeckScene>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Reaction: drag the lean from Pass to Like, then fire a burst. State machine "Reaction": triggers `like` and `pass`, number `lean` (-100 to 100). */
export const HeartOnALikeAndCrossOnAPass: Story = {
  render: function Reaction() {
    const [signals] = useState(createDeckSignals);
    const [lean, setLean] = useState(0);
    return (
      <div className="flex max-w-md flex-col gap-4">
        <div className="relative h-80 rounded-3xl bg-card shadow-sm">
          <DeckScene
            scene="Reaction"
            motion
            signals={signals}
            className="absolute inset-0"
          />
        </div>
        <div className="flex flex-col gap-2">
          <Label id="deck-lean">{`Lean of the card on top: ${lean}`}</Label>
          <Slider
            aria-labelledby="deck-lean"
            min={-100}
            max={100}
            step={1}
            value={[lean]}
            onValueChange={([value = 0]) => {
              setLean(value);
              signals.setLean(value / 100);
            }}
          />
        </div>
        <div className="flex gap-2">
          <Button
            onClick={() => {
              signals.burst("like");
            }}
          >
            Like burst
          </Button>
          <Button
            variant="outline"
            onClick={() => {
              signals.burst("pass");
            }}
          >
            Pass burst
          </Button>
        </div>
      </div>
    );
  },
};

/** Hint: the first visit's gesture, a finger dragging a small card right then left, looping. State machine "Hint", no input. */
export const SwipeHint: Story = {
  args: {
    scene: "Hint",
    className: "h-56 w-64 rounded-2xl bg-foreground/90",
    fallback: <MoveHorizontal className="size-6 text-background" />,
  },
};

/** Done: the deck went through; cards fan out under a check while confetti falls, then rest. State machine "Done": trigger `celebrate` replays it. */
export const DeckDoneCelebration: Story = {
  render: function Done() {
    const [replay, setReplay] = useState(0);
    return (
      <div className="flex flex-col items-start gap-4">
        <DeckScene
          scene="Done"
          motion
          replay={replay}
          className="h-56 w-80"
          fallback={<PartyPopper className="size-6" />}
        />
        <Button
          variant="outline"
          onClick={() => {
            setReplay((n) => n + 1);
          }}
        >
          Celebrate again
        </Button>
      </div>
    );
  },
};

/** Empty: nothing to show yet, a magnifier searching over empty cards. State machine "Empty", no input. */
export const NothingToShowYet: Story = {
  args: {
    scene: "Empty",
    className: "h-56 w-72",
    fallback: <Layers className="size-6" />,
  },
};

/** Loading: three cards fanning in and out while the deck is read. State machine "Loading", no input. */
export const DeckLoading: Story = {
  args: { scene: "Loading", className: "h-48 w-56" },
};

/** With reduced motion no scene loads: the fallback icon stays. */
export const ReducedMotionKeepsTheIcon: Story = {
  args: {
    scene: "Done",
    motion: false,
    className: "h-40 w-56",
    fallback: <PartyPopper className="size-6" />,
  },
};

/** On a phone width the scenes scale into their box. */
export const OnAPhone: Story = {
  render: () => (
    <div className="max-w-sm">
      <DeckScene
        scene="Done"
        motion
        className="aspect-[36/26] w-full"
        fallback={<PartyPopper className="size-6" />}
      />
    </div>
  ),
};
