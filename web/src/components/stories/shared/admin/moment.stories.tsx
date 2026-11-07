import type { Meta, StoryObj } from "@storybook/react-vite";

import { Moment } from "@/components/shared/admin/moment";

const NOW = new Date("2026-11-06T10:00:00Z");

const meta = {
  title: "Shared/Admin/Moment",
  component: Moment,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "A time said relatively, its exact date and hour in a tooltip: the last sign in and the invite expiries of the Admin Users table.",
      },
    },
  },
  args: { at: "2026-11-04T09:12:00Z", now: NOW, className: "text-sm" },
} satisfies Meta<typeof Moment>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A last sign in two days ago; hover or focus it for the exact time. */
export const InThePast: Story = { name: "In the past" };

/** An invite link that expires in a week. */
export const InTheFuture: Story = {
  name: "In the future",
  args: { at: "2026-11-13T10:00:00Z" },
};

/** Under a minute reads now. */
export const JustNow: Story = {
  name: "Just now",
  args: { at: "2026-11-06T09:59:40Z" },
};

/** One clock for a whole table, so every time moves together. */
export const OneClockForATable: Story = {
  name: "One clock for a table",
  render: (args) => (
    <ul className="flex flex-col gap-1 text-sm">
      {[
        "2026-11-06T09:20:00Z",
        "2026-11-05T22:00:00Z",
        "2026-10-30T08:00:00Z",
        "2026-11-09T10:00:00Z",
      ].map((at) => (
        <li key={at}>
          <Moment {...args} at={at} />
        </li>
      ))}
    </ul>
  ),
};
