import type { Meta, StoryObj } from "@storybook/react-vite";

import { SwipeHint } from "../components/deck/swipe-hint";

const meta = {
  title: "Pages/Discover/Swipe hint",
  component: SwipeHint,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The first visit's hint of Discover, over the lower part of the card until the first swipe: a Rive finger drags a small card right then left, with the words.",
      },
    },
  },
  args: { motion: true, keys: true },
} satisfies Meta<typeof SwipeHint>;

export default meta;
type Story = StoryObj<typeof meta>;

/** With a mouse or a trackpad: the arrow keys are named too. */
export const WithAKeyboard: Story = {};

/** On a touch screen: the drag only. */
export const OnATouchScreen: Story = {
  args: { keys: false },
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
};
