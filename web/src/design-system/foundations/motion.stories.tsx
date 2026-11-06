import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const meta = {
  title: "Foundations/Motion",
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Motion answers the person: it shows what changed after a click, and nothing moves for decoration. The more often a thing happens, the less it moves. One strong ease-out everywhere; exits are faster than entrances; with reduced motion, movement becomes a fade or nothing.",
      },
    },
  },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

const DURATIONS = [
  [
    "Feedback",
    "120 ms",
    "transition (the default)",
    "Hover, press, a toggled switch: tens of times a day",
  ],
  [
    "Overlay",
    "150 to 200 ms",
    "tw-animate-css: animate-in fade-in zoom-in-95",
    "Menus, tooltips, dialogs open from where they were asked",
  ],
  [
    "Panel",
    "200 to 300 ms",
    "Sheet slide-in",
    "A sheet from the side, or from the bottom on a phone",
  ],
  [
    "Patience",
    "200 ms after 400 ms",
    ".delayed-appear",
    "A spinner shows only when the wait is long enough to notice",
  ],
] as const;

/** Durations by purpose. The easing is the token ease-out, cubic-bezier(0.23, 1, 0.32, 1). */
export const Durations: Story = {
  render: () => (
    <dl className="grid max-w-4xl gap-x-6 gap-y-3 text-sm sm:grid-cols-[7rem_9rem_1fr_1fr]">
      {DURATIONS.map(([name, time, how, when]) => (
        <div key={name} className="contents">
          <dt className="font-medium">{name}</dt>
          <dd className="tabular-nums">{time}</dd>
          <dd>
            <code className="text-xs">{how}</code>
          </dd>
          <dd className="text-muted-foreground">{when}</dd>
        </div>
      ))}
    </dl>
  ),
};

/** Press the button: the panel answers in 200 ms with the system ease-out, and stays still with reduced motion. */
export const EaseOut: Story = {
  render: function EaseOutDemo() {
    const [open, setOpen] = useState(false);
    return (
      <div className="flex max-w-md flex-col items-start gap-4">
        <Button
          variant="outline"
          aria-expanded={open}
          onClick={() => {
            setOpen((value) => !value);
          }}
        >
          {open ? "Hide the answer" : "Show the answer"}
        </Button>
        <div
          className={cn(
            "w-full rounded-lg border bg-card p-4 text-sm transition-[opacity,translate] duration-200 ease-out motion-reduce:transition-none",
            open ? "translate-y-0 opacity-100" : "-translate-y-1 opacity-0",
          )}
          aria-hidden={!open}
        >
          The assistant filled 14 fields. Review them before it sends the
          application.
        </div>
      </div>
    );
  },
};
