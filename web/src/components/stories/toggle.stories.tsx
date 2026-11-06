import type { Meta, StoryObj } from "@storybook/react-vite";
import { EyeOffIcon, HeartIcon, HouseIcon } from "lucide-react";

import { previewState, StateMatrix } from "@/design-system/state-matrix";

import { Toggle } from "@/components/ui/toggle";

const VARIANTS = ["outline", "default"] as const;

const meta = {
  title: "Components/Actions/Toggle",
  component: Toggle,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "A button that stays pressed: a filter of the list in view that is on or off, like Remote only. A setting that is saved is a Switch, a choice among a few options is a ToggleGroup.",
      },
    },
  },
  args: { children: "Remote only", variant: "outline" },
  argTypes: {
    variant: { control: "inline-radio", options: VARIANTS },
    size: { control: "inline-radio", options: ["sm", "default", "lg"] },
  },
} satisfies Meta<typeof Toggle>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A filter above the jobs list. */
export const Typical: Story = {};

/** In priority order, each off then on. Outline where the toggle sits among fields and buttons, default (borderless) inside a toolbar that already draws the edge. */
export const Variants: Story = {
  render: () => (
    <div className="flex flex-col gap-3">
      {VARIANTS.map((variant) => (
        <div key={variant} className="flex flex-wrap items-center gap-3">
          <Toggle variant={variant}>Remote only</Toggle>
          <Toggle variant={variant} defaultPressed>
            Remote only
          </Toggle>
        </div>
      ))}
    </div>
  ),
};

/** sm in a toolbar of a table, default beside fields, lg alone on a touch-first page. */
export const Sizes: Story = {
  render: () => (
    <div className="flex flex-wrap items-center gap-3">
      <Toggle variant="outline" size="sm">
        Remote only
      </Toggle>
      <Toggle variant="outline">Remote only</Toggle>
      <Toggle variant="outline" size="lg">
        Remote only
      </Toggle>
    </div>
  ),
};

/** An icon leads the label. An icon alone needs an accessible name. */
export const WithIcon: Story = {
  render: () => (
    <div className="flex flex-wrap items-center gap-3">
      <Toggle variant="outline">
        <HouseIcon />
        Remote only
      </Toggle>
      <Toggle variant="outline" defaultPressed>
        <HeartIcon />
        Liked only
      </Toggle>
      <Toggle variant="outline" aria-label="Hide passed postings">
        <EyeOffIcon />
      </Toggle>
    </div>
  ),
};

/** Every variant in every state, pinned. On is the pressed state, the one that filters. */
export const States: Story = {
  render: () => (
    <StateMatrix
      rows={VARIANTS}
      states={["default", "hover", "focus", "on", "disabled"]}
      render={(variant, state) => (
        <Toggle
          variant={variant}
          defaultPressed={state === "on"}
          {...previewState(state)}
        >
          Remote only
        </Toggle>
      )}
    />
  ),
};
