import type { Meta, StoryObj } from "@storybook/react-vite";

import { PayText } from "@/components/shared/jobs/badges";

const meta = {
  title: "Shared/Jobs/Pay text",
  component: PayText,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The pay a posting states, in words with separators, or a quiet Pay not given: in a row of the Jobs list, on the map's cards and under a posting's title.",
      },
    },
  },
  args: {
    job: {
      pay_min: 55000,
      pay_max: 65000,
      pay_currency: "EUR",
      pay_period: "year",
    },
  },
} satisfies Meta<typeof PayText>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A yearly range, the most common case. */
export const Typical: Story = { name: "A yearly range" };

/** A freelance mission's day rate. */
export const DayRate: Story = {
  name: "A day rate",
  args: {
    job: {
      pay_min: 600,
      pay_max: 650,
      pay_currency: "EUR",
      pay_period: "day",
    },
  },
};

/** One figure when the lowest and the highest are the same. */
export const OneFigure: Story = {
  name: "One figure a month",
  args: {
    job: {
      pay_min: 1400,
      pay_max: 1400,
      pay_currency: "EUR",
      pay_period: "month",
    },
  },
};

/** A posting that states no pay: the app says so instead of guessing. */
export const NotGiven: Story = {
  name: "Pay not given",
  args: { job: {} },
};

/** In a narrow column, as on a phone: the words wrap, the figures stay together. */
export const OnAPhone: Story = {
  name: "On a phone",
  render: (args) => (
    <p className="max-w-40 text-sm">
      Lumen Freight, Lyon, <PayText {...args} className="font-medium" />
    </p>
  ),
};
