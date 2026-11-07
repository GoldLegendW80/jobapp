import type { Meta, StoryObj } from "@storybook/react-vite";
import { MemoryRouter } from "react-router";

import { DeckTopBar } from "../components/deck/deck-top-bar";

const meta = {
  title: "Pages/Discover/Deck top bar",
  component: DeckTopBar,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Above the deck of Discover: the page's name, the postings left, and the mode a Like runs in, linked to the Applications settings; Full auto stands out in the brand colour.",
      },
    },
  },
  args: { mode: "review", left: 12 },
  decorators: [
    (Story) => (
      <MemoryRouter>
        <Story />
      </MemoryRouter>
    ),
  ],
} satisfies Meta<typeof DeckTopBar>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Review first: a Like waits for the person's approval before anything is sent. */
export const ReviewFirst: Story = {};

/** Full auto: a Like is the approval to send. */
export const FullAuto: Story = { args: { mode: "full_auto", left: 1 } };

/** No card on screen: no count. */
export const NothingLeft: Story = { args: { left: undefined } };

/** On a phone width, the mode wraps under the name. */
export const OnAPhone: Story = {
  args: { mode: "full_auto" },
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
};
