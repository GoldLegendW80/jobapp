import type { Meta, StoryObj } from "@storybook/react-vite";

import { FitDial } from "../components/deck/fit-dial";

const meta = {
  title: "Pages/Discover/Fit dial",
  component: FitDial,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The fit of a deck card as its hero figure: the number inside a ring filled to it, on the colour scale of the fit badges. Shown at the top right of each card of Discover.",
      },
    },
  },
  args: { fit: 91 },
} satisfies Meta<typeof FitDial>;

export default meta;
type Story = StoryObj<typeof meta>;

/** 85 and up: a strong match. */
export const StrongFit: Story = {};

/** 70 to 84: a good one. */
export const GoodFit: Story = { args: { fit: 74 } };

/** 50 to 69: fair. */
export const FairFit: Story = { args: { fit: 58 } };

/** Under 50: low. */
export const LowFit: Story = { args: { fit: 31 } };

/** Not scored yet. */
export const Unscored: Story = { args: { fit: null } };
