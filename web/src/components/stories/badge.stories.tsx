import type { Meta, StoryObj } from "@storybook/react-vite";
import { CheckIcon, HouseIcon, MapPinIcon, TargetIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";

const VARIANTS = [
  "outline",
  "secondary",
  "default",
  "destructive",
  "ghost",
  "link",
] as const;

const meta = {
  title: "Components/Actions/Badge",
  component: Badge,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "A short fact read at a glance: a posting's contract and work mode, a count, the state of a document. One to three words; it is never the thing to click, a Button is.",
      },
    },
  },
  args: { children: "Permanent", variant: "outline" },
  argTypes: {
    variant: { control: "inline-radio", options: VARIANTS },
  },
} satisfies Meta<typeof Badge>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A fact of a posting, in the row under its title. */
export const Typical: Story = {};

/** In priority order. Outline for the facts of a posting, secondary for counts and quiet states, default for the one that stands out (the CV in use), destructive only for a failure. Ghost and link are left for rare cases. */
export const Variants: Story = {
  render: () => (
    <div className="flex flex-wrap items-center gap-3">
      <Badge variant="outline">Fixed term</Badge>
      <Badge variant="secondary">3 new</Badge>
      <Badge>Default</Badge>
      <Badge variant="destructive">Failed</Badge>
      <Badge variant="ghost">Internal</Badge>
      <Badge variant="link">Lumen Freight</Badge>
    </div>
  ),
};

/** An icon leads and only when it adds meaning the words do not: where, how well, or done. */
export const WithIcon: Story = {
  render: () => (
    <div className="flex flex-wrap items-center gap-3">
      <Badge variant="outline">
        <MapPinIcon />
        Lyon
      </Badge>
      <Badge variant="outline">
        <HouseIcon />
        Remote
      </Badge>
      <Badge variant="secondary">
        <TargetIcon />
        Fit 82
      </Badge>
      <Badge>
        <CheckIcon />
        Ready
      </Badge>
    </div>
  ),
};

/** The facts row of a posting card: badges wrap as a row, each stays on one line. */
export const InARow: Story = {
  render: () => (
    <div className="flex max-w-xs flex-col gap-2">
      <p className="text-sm font-medium">Product designer, Atelier Brume</p>
      <div className="flex flex-wrap gap-2">
        <Badge variant="outline">Permanent</Badge>
        <Badge variant="outline">Hybrid</Badge>
        <Badge variant="outline">Nantes</Badge>
        <Badge variant="secondary">48,000 to 55,000 € a year</Badge>
      </div>
    </div>
  ),
};

/** A badge never wraps by itself. A value that can run long, like a template tag, opts in with whitespace-normal and stays inside its column. */
export const LongText: Story = {
  render: () => (
    <div className="flex max-w-40 flex-col items-start gap-2 text-xs text-muted-foreground">
      As it comes, on one line:
      <Badge variant="outline">Easy for hiring software to read</Badge>
      With whitespace-normal:
      <Badge variant="outline" className="max-w-full whitespace-normal">
        Easy for hiring software to read
      </Badge>
    </div>
  ),
};
