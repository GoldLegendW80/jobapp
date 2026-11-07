import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { DeckControls } from "../components/deck/deck-controls";

const meta = {
  title: "Pages/Discover/Deck controls",
  component: DeckControls,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Pass, Undo last and Like under the deck of Discover, round and within a thumb's reach, each named under it (a tap on the name acts too).",
      },
    },
  },
  args: {
    hasCard: true,
    canUndo: false,
    busy: false,
    likeRefused: false,
    onPass: fn(),
    onLike: fn(),
    onUndo: fn(),
  },
} satisfies Meta<typeof DeckControls>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A card to swipe, nothing to undo yet. */
export const ReadyToSwipe: Story = {};

/** After a swipe: Undo last can bring it back. */
export const AfterASwipe: Story = { args: { canUndo: true } };

/** A swipe under way: all three wait, Like shows it works. */
export const SwipeUnderWay: Story = { args: { canUndo: true, busy: true } };

/** A refused Like: Like stays off, Pass is offered. */
export const LikeRefused: Story = { args: { likeRefused: true } };

/** The deck emptied by the last swipe: only Undo last stays. */
export const OnlyUndoLeft: Story = { args: { hasCard: false, canUndo: true } };

/** On a phone width. */
export const OnAPhone: Story = {
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
};
