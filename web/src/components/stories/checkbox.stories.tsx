import type { Meta, StoryObj } from "@storybook/react-vite";

import { previewState, StateMatrix } from "@/design-system/state-matrix";

import { Checkbox } from "@/components/ui/checkbox";
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldError,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field";

const CONTRACTS = [
  ["permanent", "Permanent"],
  ["fixed-term", "Fixed term"],
  ["freelance", "Freelance mission"],
  ["internship", "Internship"],
  ["apprenticeship", "Apprenticeship"],
] as const;

const meta = {
  title: "Components/Inputs/Checkbox",
  component: Checkbox,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "A yes or no that takes effect when the form is saved, or one of several answers that can all be true. A setting that applies at once is a Switch. The label is the clickable sentence beside the box.",
      },
    },
  },
  args: { defaultChecked: true },
} satisfies Meta<typeof Checkbox>;

export default meta;
type Story = StoryObj<typeof meta>;

/** One yes or no of the application settings, its label beside it. */
export const Typical: Story = {
  render: (args) => (
    <Field orientation="horizontal">
      <Checkbox id="typical-review" {...args} />
      <FieldLabel htmlFor="typical-review" className="font-normal">
        Let me review each filled form before it is sent
      </FieldLabel>
    </Field>
  ),
};

/** Several answers that can all be true sit in a FieldSet with a legend. */
export const Group: Story = {
  render: () => (
    <FieldSet>
      <FieldLegend variant="label">Contracts (choose any)</FieldLegend>
      <FieldGroup data-slot="checkbox-group">
        {CONTRACTS.map(([value, label]) => (
          <Field key={value} orientation="horizontal">
            <Checkbox
              id={`group-${value}`}
              defaultChecked={value === "permanent" || value === "freelance"}
            />
            <FieldLabel htmlFor={`group-${value}`} className="font-normal">
              {label}
            </FieldLabel>
          </Field>
        ))}
      </FieldGroup>
    </FieldSet>
  ),
};

/** A sentence under the label says what ticking it changes. */
export const WithDescription: Story = {
  render: () => (
    <Field orientation="horizontal" className="max-w-md">
      <Checkbox
        id="description-photo"
        aria-describedby="description-photo-text"
      />
      <FieldContent>
        <FieldLabel htmlFor="description-photo">
          Put my photo on my CV
        </FieldLabel>
        <FieldDescription id="description-photo-text">
          Some countries expect one, others prefer none. The app leaves it out
          unless you tick this.
        </FieldDescription>
      </FieldContent>
    </Field>
  ),
};

/** Unticked, ticked and partly ticked (the select all of a list with some rows chosen), in every state, pinned. */
export const States: Story = {
  render: () => (
    <StateMatrix
      rows={["unchecked", "checked", "indeterminate"] as const}
      states={["default", "focus", "invalid", "disabled"]}
      render={(row, state) => (
        <Checkbox
          aria-label={`Select this posting, ${row}, ${state}`}
          defaultChecked={
            row === "checked"
              ? true
              : row === "indeterminate"
                ? "indeterminate"
                : false
          }
          {...(state === "invalid"
            ? { "aria-invalid": true }
            : previewState(state))}
        />
      )}
    />
  ),
};

/** A label that can wrap goes in a FieldContent, which keeps the box on its first line instead of its middle. */
export const LongText: Story = {
  render: () => (
    <Field orientation="horizontal" className="max-w-xs">
      <Checkbox id="long-terms" />
      <FieldContent>
        <FieldLabel htmlFor="long-terms" className="font-normal">
          I agree that Lumen Freight keeps my application for two years to
          consider me for other roles
        </FieldLabel>
      </FieldContent>
    </Field>
  ),
};

/** A group that needs one answer at least says so under its options. */
export const Error: Story = {
  render: () => (
    <FieldSet data-invalid="true">
      <FieldLegend variant="label">Contracts (choose any)</FieldLegend>
      <FieldGroup data-slot="checkbox-group">
        {CONTRACTS.slice(0, 3).map(([value, label]) => (
          <Field key={value} orientation="horizontal" data-invalid="true">
            <Checkbox
              id={`error-${value}`}
              aria-invalid="true"
              aria-describedby="error-contracts-message"
            />
            <FieldLabel htmlFor={`error-${value}`} className="font-normal">
              {label}
            </FieldLabel>
          </Field>
        ))}
      </FieldGroup>
      <FieldError id="error-contracts-message">
        Choose at least one contract, or the search finds nothing.
      </FieldError>
    </FieldSet>
  ),
};

/** An answer the person cannot give here, with a sentence that says why. */
export const Disabled: Story = {
  render: () => (
    <Field
      orientation="horizontal"
      data-disabled="true"
      aria-disabled="true"
      className="max-w-md"
    >
      <Checkbox
        id="disabled-auto"
        disabled
        aria-describedby="disabled-auto-why"
      />
      <FieldContent>
        <FieldLabel htmlFor="disabled-auto">
          Apply automatically from a fit of 85
        </FieldLabel>
        <FieldDescription id="disabled-auto-why">
          Full auto needs a model key. Add one in your Account.
        </FieldDescription>
      </FieldContent>
    </Field>
  ),
};
