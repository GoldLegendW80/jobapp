import type { Meta, StoryObj } from "@storybook/react-vite";

import { StateMatrix } from "@/design-system/state-matrix";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldError,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSeparator,
  FieldSet,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupInput,
  InputGroupText,
} from "@/components/ui/input-group";
import { NativeSelect } from "@/components/ui/native-select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";

const meta = {
  title: "Components/Forms/Field",
  component: Field,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The layout of every form: a label, its control, a sentence that helps and the error, spaced the same on every page. FieldGroup stacks fields, FieldSet with a FieldLegend groups the ones that answer one question. A form whose values react-hook-form holds wraps the same parts in Form.",
      },
    },
  },
  args: { orientation: "vertical" },
  argTypes: {
    orientation: {
      control: "inline-radio",
      options: ["vertical", "horizontal", "responsive"],
    },
  },
} satisfies Meta<typeof Field>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The search settings of a profile: fields stacked in a FieldGroup, the action last. */
export const Typical: Story = {
  render: () => (
    <form
      className="max-w-md"
      onSubmit={(event) => {
        event.preventDefault();
      }}
    >
      <FieldGroup>
        <Field>
          <FieldLabel htmlFor="typical-titles">Job titles</FieldLabel>
          <Input
            id="typical-titles"
            aria-describedby="typical-titles-text"
            defaultValue="Product designer, UX designer"
          />
          <FieldDescription id="typical-titles-text">
            Separate them with commas. The search looks for these words in the
            title of a posting.
          </FieldDescription>
        </Field>
        <Field>
          <FieldLabel htmlFor="typical-salary">Desired salary</FieldLabel>
          <InputGroup className="max-w-60">
            <InputGroupAddon>
              <InputGroupText>€</InputGroupText>
            </InputGroupAddon>
            <InputGroupInput
              id="typical-salary"
              inputMode="numeric"
              defaultValue="52000"
            />
            <InputGroupAddon align="inline-end">
              <InputGroupText>a year</InputGroupText>
            </InputGroupAddon>
          </InputGroup>
        </Field>
        <Field>
          <FieldLabel htmlFor="typical-notice">Notice period</FieldLabel>
          <NativeSelect id="typical-notice" defaultValue="2">
            <option value="0">None, I can start now</option>
            <option value="1">1 month</option>
            <option value="2">2 months</option>
            <option value="3">3 months</option>
          </NativeSelect>
        </Field>
        <Field orientation="horizontal">
          <FieldContent>
            <FieldLabel htmlFor="typical-remote">Remote only</FieldLabel>
            <FieldDescription id="typical-remote-text">
              Only postings you can do from home reach your deck.
            </FieldDescription>
          </FieldContent>
          <Switch id="typical-remote" aria-describedby="typical-remote-text" />
        </Field>
        <Field orientation="horizontal">
          <Button type="submit">Save search settings</Button>
        </Field>
      </FieldGroup>
    </form>
  ),
};

/** Vertical by default, the label above. Horizontal for a switch or a checkbox beside its label. Responsive puts the label beside the control when the FieldGroup is wide, above it on a phone. */
export const Orientations: Story = {
  render: () => (
    <FieldGroup className="max-w-xl">
      <Field orientation="vertical">
        <FieldLabel htmlFor="orientation-vertical">
          Notice period (weeks)
        </FieldLabel>
        <Input
          id="orientation-vertical"
          inputMode="numeric"
          defaultValue="8"
          className="max-w-32"
        />
      </Field>
      <Field orientation="horizontal">
        <Checkbox id="orientation-horizontal" defaultChecked />
        <FieldLabel htmlFor="orientation-horizontal" className="font-normal">
          Let me review each filled form before it is sent
        </FieldLabel>
      </Field>
      <Field orientation="responsive">
        <FieldContent>
          <FieldLabel htmlFor="orientation-responsive">
            Daily rate (freelance)
          </FieldLabel>
          <FieldDescription>What you ask for a day of work.</FieldDescription>
        </FieldContent>
        <InputGroup className="sm:max-w-48">
          <InputGroupInput
            id="orientation-responsive"
            inputMode="numeric"
            defaultValue="550"
          />
          <InputGroupAddon align="inline-end">
            <InputGroupText>€ a day</InputGroupText>
          </InputGroupAddon>
        </InputGroup>
      </Field>
    </FieldGroup>
  ),
};

/** Fields that answer one question sit in a FieldSet; its legend is the question, its description what the answers do. */
export const WithFieldSet: Story = {
  render: () => (
    <FieldSet className="max-w-md">
      <FieldLegend>Pay</FieldLegend>
      <FieldDescription>
        Postings below these figures score lower. Leave both empty if you would
        rather not say.
      </FieldDescription>
      <FieldGroup>
        <Field>
          <FieldLabel htmlFor="set-salary">Yearly salary</FieldLabel>
          <Input id="set-salary" inputMode="numeric" defaultValue="52000" />
        </Field>
        <Field>
          <FieldLabel htmlFor="set-rate">Daily rate (freelance)</FieldLabel>
          <Input id="set-rate" inputMode="numeric" />
        </Field>
      </FieldGroup>
    </FieldSet>
  ),
};

/** Every state of a field, pinned. data-invalid on the Field turns its label red, aria-invalid on the control turns its border red; data-disabled dims the label, disabled the control. */
export const States: Story = {
  render: () => (
    <StateMatrix
      rows={["vertical", "horizontal"] as const}
      states={["default", "invalid", "disabled"]}
      render={(row, state) => {
        const id = `states-${row}-${state}`;
        const invalid = state === "invalid";
        const disabled = state === "disabled";
        const fieldState = {
          "data-invalid": invalid ? "true" : undefined,
          "data-disabled": disabled ? "true" : undefined,
          "aria-disabled": disabled ? true : undefined,
        };
        return row === "vertical" ? (
          <Field className="w-56" {...fieldState}>
            <FieldLabel htmlFor={id}>Notice period (weeks)</FieldLabel>
            <Input
              id={id}
              inputMode="numeric"
              defaultValue={invalid ? "two" : "8"}
              aria-invalid={invalid ? true : undefined}
              disabled={disabled}
            />
            {invalid ? (
              <FieldError>Write a number of weeks, like 8.</FieldError>
            ) : (
              <FieldDescription>Read from your CV.</FieldDescription>
            )}
          </Field>
        ) : (
          <Field orientation="horizontal" className="w-56" {...fieldState}>
            <Checkbox
              id={id}
              aria-invalid={invalid ? true : undefined}
              disabled={disabled}
            />
            <FieldContent>
              <FieldLabel htmlFor={id} className="font-normal">
                I agree to the terms of Lumen Freight
              </FieldLabel>
            </FieldContent>
          </Field>
        );
      }}
    />
  ),
};

/** Several errors on one field come as a list, each a sentence that says what to change. */
export const Error: Story = {
  render: () => (
    <Field data-invalid="true" className="max-w-md">
      <FieldLabel htmlFor="error-letter">
        Cover letter for Atelier Brume
      </FieldLabel>
      <Textarea
        id="error-letter"
        aria-invalid="true"
        aria-describedby="error-letter-message"
        defaultValue="Dear hiring team, I would like to apply."
      />
      <FieldError
        id="error-letter-message"
        errors={[
          { message: "The letter is shorter than 400 characters." },
          { message: "It does not name the role you apply for." },
        ]}
      />
    </Field>
  ),
};

/** A label and a description long enough to wrap keep the control aligned with their first line. */
export const LongText: Story = {
  render: () => (
    <Field orientation="horizontal" className="max-w-sm">
      <FieldContent>
        <FieldLabel htmlFor="long-unwanted">
          Hide postings from companies I already applied to this year
        </FieldLabel>
        <FieldDescription id="long-unwanted-text">
          A company stays hidden for twelve months after your application, even
          when it posts a role with a different title.
        </FieldDescription>
      </FieldContent>
      <Switch id="long-unwanted" aria-describedby="long-unwanted-text" />
    </Field>
  ),
};

/** Two ways to give the same thing, split by a FieldSeparator with the word between them. */
export const WithSeparator: Story = {
  render: () => (
    <FieldGroup className="max-w-md">
      <Field>
        <FieldLabel htmlFor="separator-cv">Your CV</FieldLabel>
        <Input id="separator-cv" type="file" accept=".pdf,.docx" />
      </Field>
      <FieldSeparator>or</FieldSeparator>
      <Field>
        <FieldLabel htmlFor="separator-text">Paste your CV as text</FieldLabel>
        <Textarea id="separator-text" className="min-h-24" />
      </Field>
    </FieldGroup>
  ),
};
