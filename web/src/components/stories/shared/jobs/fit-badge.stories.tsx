import type { Meta, StoryObj } from "@storybook/react-vite";

import { FitBadge } from "@/components/shared/jobs/badges";

const meta = {
  title: "Shared/Jobs/Fit badge",
  component: FitBadge,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "How well a posting fits the person, on the histogram's color scale and in words: in the Jobs list, on the map, on a posting and in a search's summary.",
      },
    },
  },
  args: { fit: 82 },
} satisfies Meta<typeof FitBadge>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A good fit, as a row of the list shows it. */
export const Typical: Story = { name: "A good fit" };

/** The four tones: top from 85, good from 70, fair from 50, low below. */
export const EveryTone: Story = {
  name: "Every tone",
  render: () => (
    <div className="flex flex-wrap items-center gap-3">
      <FitBadge fit={91} />
      <FitBadge fit={76} />
      <FitBadge fit={58} />
      <FitBadge fit={31} />
    </div>
  ),
};

/** A posting the app has not scored: it says so, in a quiet style, and never guesses. */
export const NotScored: Story = {
  name: "Not scored yet",
  args: { fit: null },
};

/** A fit the person set by hand says "manual", with a hint on hover. */
export const SetByHand: Story = {
  name: "Set by hand",
  args: { fit: 64, overridden: true },
};
