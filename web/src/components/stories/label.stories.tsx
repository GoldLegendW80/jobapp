import type { Meta, StoryObj } from "@storybook/react-vite";
import { LockIcon } from "lucide-react";

import { StateMatrix } from "@/design-system/state-matrix";

import { Checkbox } from "@/components/ui/checkbox";
import { Field, FieldContent, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";

const meta = {
  title: "Components/Inputs/Label",
  component: Label,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The visible name of a control, tied to it by htmlFor. Inside a form use FieldLabel, which is this Label with the spacing of a Field; Label alone names a control that stands outside a form, like the zoom of the photo editor.",
      },
    },
  },
  args: { children: "Notice period", htmlFor: "typical-notice" },
} satisfies Meta<typeof Label>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A label above its input; clicking it focuses the input. */
export const Typical: Story = {
  render: (args) => (
    <div className="flex max-w-xs flex-col gap-2">
      <Label {...args} />
      <Input id="typical-notice" defaultValue="2 months" />
    </div>
  ),
};

/** Beside a checkbox or a switch, the label comes after the control and the whole row is clickable. */
export const WithControl: Story = {
  render: () => (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-2">
        <Checkbox id="control-remote" defaultChecked />
        <Label htmlFor="control-remote">Remote only</Label>
      </div>
      <div className="flex items-center gap-2">
        <Switch id="control-daily" />
        <Label htmlFor="control-daily">Find new postings every morning</Label>
      </div>
    </div>
  ),
};

/** An icon leads when it adds meaning, like a field only you can see. */
export const WithIcon: Story = {
  render: () => (
    <div className="flex max-w-xs flex-col gap-2">
      <Label htmlFor="icon-salary">
        <LockIcon aria-hidden="true" className="size-3.5" />
        Current salary, never shared
      </Label>
      <Input id="icon-salary" inputMode="numeric" defaultValue="47000" />
    </div>
  ),
};

/** A label dims with its control when the control comes first and is disabled (peer-disabled). */
export const States: Story = {
  render: () => (
    <StateMatrix
      rows={["checkbox", "switch"] as const}
      states={["default", "disabled"]}
      render={(row, state) => {
        const id = `states-${row}-${state}`;
        return (
          <div className="flex items-center gap-2">
            {row === "checkbox" ? (
              <Checkbox id={id} disabled={state === "disabled"} />
            ) : (
              <Switch id={id} disabled={state === "disabled"} />
            )}
            <Label htmlFor={id}>Remote only</Label>
          </div>
        );
      }}
    />
  ),
};

/** Label has a line height of one, so a second line sits tight against the first. A label that can wrap belongs in a Field: its FieldLabel spaces the lines, and a FieldContent around it keeps the box on the first one. */
export const LongText: Story = {
  render: () => (
    <div className="flex max-w-56 flex-col gap-6 text-xs text-muted-foreground">
      <div className="flex flex-col gap-2">
        Label:
        <div className="flex items-center gap-2 text-foreground">
          <Checkbox id="long-label" />
          <Label htmlFor="long-label">
            Let me review each filled form before it is sent
          </Label>
        </div>
      </div>
      <div className="flex flex-col gap-2">
        FieldLabel in a Field:
        <Field orientation="horizontal" className="text-foreground">
          <Checkbox id="long-field-label" />
          <FieldContent>
            <FieldLabel htmlFor="long-field-label">
              Let me review each filled form before it is sent
            </FieldLabel>
          </FieldContent>
        </Field>
      </div>
    </div>
  ),
};
