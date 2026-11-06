import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";

import { StateMatrix } from "@/design-system/state-matrix";

import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Slider } from "@/components/ui/slider";

/** A labelled slider whose value is read out beside its label, as the fit thresholds are. */
function Threshold({
  id,
  label,
  description,
  initial,
  step = 1,
  disabled,
}: {
  id: string;
  label: string;
  description: string;
  initial: number;
  step?: number;
  disabled?: boolean;
}) {
  const [value, setValue] = useState(initial);
  return (
    <Field
      className="max-w-sm"
      {...(disabled ? { "data-disabled": "true", "aria-disabled": true } : {})}
    >
      <div className="flex items-baseline justify-between gap-2">
        <FieldLabel id={`${id}-label`}>{label}</FieldLabel>
        <output className="text-sm text-muted-foreground tabular-nums">
          {value} / 100
        </output>
      </div>
      <Slider
        aria-labelledby={`${id}-label`}
        aria-describedby={`${id}-text`}
        min={0}
        max={100}
        step={step}
        value={[value]}
        disabled={disabled}
        onValueChange={([next]) => {
          if (next !== undefined) setValue(next);
        }}
      />
      <FieldDescription id={`${id}-text`}>{description}</FieldDescription>
    </Field>
  );
}

const meta = {
  title: "Components/Inputs/Slider",
  component: Slider,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "A number on a known scale where the rough position matters more than the exact figure: a fit threshold, the points a requirement takes off, the zoom of a photo. The value is always written beside the label; a figure the person knows exactly is an Input.",
      },
    },
  },
  args: { defaultValue: [70], max: 100, step: 1, "aria-label": "Minimum fit" },
} satisfies Meta<typeof Slider>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The minimum fit of the shortlist, its value beside the label. */
export const Typical: Story = {
  render: () => (
    <Threshold
      id="typical-fit"
      label="Minimum fit"
      description="Postings scoring at least this are proposed for the shortlist."
      initial={70}
    />
  ),
};

/** A coarse step when small differences mean nothing: the points a requirement takes off move by 5. */
export const Steps: Story = {
  render: () => (
    <Threshold
      id="steps-points"
      label="Points taken off"
      description="Taken from the fit of a posting that asks for a requirement you do not meet."
      initial={20}
      step={5}
    />
  ),
};

/** Every state, pinned. Focus is drawn on the thumb, so this story focuses it itself (play); hover grows the same ring. */
export const States: Story = {
  render: () => (
    <StateMatrix
      rows={["fit"] as const}
      states={["default", "focus", "disabled"]}
      render={(_, state) => (
        <Slider
          className="w-40"
          aria-label={`Minimum fit, ${state}`}
          defaultValue={[70]}
          disabled={state === "disabled"}
        />
      )}
    />
  ),
  play: ({ canvasElement }) => {
    canvasElement
      .querySelector<HTMLElement>('[aria-label="Minimum fit, focus"]')
      ?.focus();
  },
};

/** At either end the thumb stays inside the track, and the value says what the end means. */
export const Ends: Story = {
  render: () => (
    <div className="flex flex-col gap-8">
      <Threshold
        id="ends-low"
        label="Show postings from"
        description="At 0 every posting reaches your deck."
        initial={0}
      />
      <Threshold
        id="ends-high"
        label="Apply automatically from"
        description="At 100 nothing is applied to without you."
        initial={100}
      />
    </div>
  ),
};

/** A threshold the person cannot move yet, with a sentence that says why. */
export const Disabled: Story = {
  render: () => (
    <Threshold
      id="disabled-auto"
      label="Apply automatically from"
      description="Full auto is off. Turn it on to choose from which fit the app applies."
      initial={85}
      disabled
    />
  ),
};
