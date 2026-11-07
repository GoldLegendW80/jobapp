import type { Meta, StoryObj } from "@storybook/react-vite";

import { cn } from "@/lib/utils";

const meta = {
  title: "Foundations/Shape and depth",
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Soft and round, one rule: every action is a pill, fields are 12 px, cards 20 px, dialogs 24 px. Depth comes from white on grey: cards sit on the canvas without a border or a shadow, and only what floats above the page casts one.",
      },
    },
  },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

const RADII = [
  ["rounded-sm", "8px", "Menu items, Kbd, small tiles"],
  ["rounded-md", "12px", "Input, Select, Textarea, menus, tooltips"],
  ["rounded-lg", "16px", "Table, Alert, Empty, panels inside a card"],
  ["rounded-xl", "20px", "Card"],
  ["rounded-2xl", "24px", "Dialog, AlertDialog"],
  ["rounded-full", "pill", "Button, Toggle, Tabs, Badge, Switch, Avatar"],
] as const;

/** The same box at every step, with the components that use it. */
export const Radius: Story = {
  render: () => (
    <div className="grid gap-6 sm:grid-cols-3 lg:grid-cols-6">
      {RADII.map(([token, value, users]) => (
        <figure key={token} className="flex flex-col gap-3">
          <div className={cn("h-20 border-2 border-primary bg-card", token)} />
          <figcaption className="flex flex-col gap-0.5">
            <code className="text-sm">
              {token}, {value}
            </code>
            <span className="text-xs text-muted-foreground">{users}</span>
          </figcaption>
        </figure>
      ))}
    </div>
  ),
};

const LEVELS = [
  ["Canvas", "bg-background", "The page itself"],
  ["Card", "bg-card", "Cards: white on the canvas, no border, no shadow"],
  ["Raised", "shadow-sm", "The Slider and Switch thumbs"],
  ["Floating", "shadow-md", "Dropdown menus, popovers"],
  ["Overlay", "shadow-lg", "Dialog, Sheet, AlertDialog, above a dimmed page"],
] as const;

/** Five levels on the canvas. A card never casts a shadow: white on grey already lifts it. */
export const Elevation: Story = {
  render: () => (
    <div className="grid gap-6 rounded-xl bg-background p-6 sm:grid-cols-2 lg:grid-cols-5">
      {LEVELS.map(([name, classes, users]) => (
        <figure key={name} className="flex flex-col gap-3">
          <div className={cn("h-20 rounded-xl bg-card", classes)} />
          <figcaption className="flex flex-col gap-0.5">
            <span className="text-sm font-medium">{name}</span>
            <code className="text-xs">{classes}</code>
            <span className="text-xs text-muted-foreground">{users}</span>
          </figcaption>
        </figure>
      ))}
    </div>
  ),
};
