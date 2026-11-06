import type { Meta, StoryObj } from "@storybook/react-vite";

import { previewState, StateMatrix } from "@/design-system/state-matrix";

import {
  Field,
  FieldDescription,
  FieldError,
  FieldGroup,
  FieldLabel,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";

const meta = {
  title: "Components/Inputs/Input",
  component: Input,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "One line of text the person types: a job title, an e-mail address, a number of weeks. Always inside a Field with a visible label; a unit or a button beside the text makes it an InputGroup.",
      },
    },
  },
  args: { placeholder: "For example: product designer" },
} satisfies Meta<typeof Input>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A job title the search looks for, labelled by a FieldLabel. */
export const Typical: Story = {
  render: (args) => (
    <Field className="max-w-sm">
      <FieldLabel htmlFor="typical-title">Job title</FieldLabel>
      <Input id="typical-title" {...args} />
    </Field>
  ),
};

/** The type brings the right keyboard on a phone and lets the browser help: email, number with inputMode, url, date, file. */
export const Types: Story = {
  render: () => (
    <FieldGroup className="max-w-sm">
      <Field>
        <FieldLabel htmlFor="types-email">E-mail address</FieldLabel>
        <Input
          id="types-email"
          type="email"
          autoComplete="email"
          defaultValue="camille.martin@example.org"
        />
      </Field>
      <Field>
        <FieldLabel htmlFor="types-notice">Notice period (weeks)</FieldLabel>
        <Input
          id="types-notice"
          inputMode="numeric"
          autoComplete="off"
          className="max-w-32"
          defaultValue="8"
        />
      </Field>
      <Field>
        <FieldLabel htmlFor="types-portfolio">Portfolio</FieldLabel>
        <Input
          id="types-portfolio"
          type="url"
          placeholder="https://camille-martin.example.org"
        />
      </Field>
      <Field>
        <FieldLabel htmlFor="types-start">Available from</FieldLabel>
        <Input
          id="types-start"
          type="date"
          className="max-w-48"
          defaultValue="2027-01-04"
        />
      </Field>
      <Field>
        <FieldLabel htmlFor="types-cv">Your CV</FieldLabel>
        <Input id="types-cv" type="file" accept=".pdf,.docx" />
        <FieldDescription>A PDF or a Word document.</FieldDescription>
      </Field>
    </FieldGroup>
  ),
};

/** Empty and filled, in every state, pinned. Invalid is aria-invalid on the input, set with data-invalid on its Field. */
export const States: Story = {
  render: () => (
    <StateMatrix
      rows={["empty", "filled"] as const}
      states={["default", "focus", "invalid", "disabled"]}
      render={(row, state) => (
        <Input
          aria-label={`Job title, ${row}, ${state}`}
          className="w-44"
          placeholder="For example: designer"
          defaultValue={row === "filled" ? "Product designer" : undefined}
          {...(state === "invalid"
            ? { "aria-invalid": true }
            : previewState(state))}
        />
      )}
    />
  ),
};

/** A value longer than the box scrolls inside it; the field keeps its width. */
export const LongText: Story = {
  render: () => (
    <Field className="max-w-xs">
      <FieldLabel htmlFor="long-title">Job title</FieldLabel>
      <Input
        id="long-title"
        defaultValue="Senior product designer, design systems and accessibility"
      />
    </Field>
  ),
};

/** The error says what to type, under the input, in place of the description. */
export const Error: Story = {
  render: () => (
    <Field data-invalid="true" className="max-w-sm">
      <FieldLabel htmlFor="error-email">E-mail address</FieldLabel>
      <Input
        id="error-email"
        type="email"
        aria-invalid="true"
        aria-describedby="error-email-message"
        defaultValue="camille.martin@example"
      />
      <FieldError id="error-email-message">
        Write a full address, like camille.martin@example.org.
      </FieldError>
    </Field>
  ),
};

/** A value the person cannot change here, with a sentence that says where it comes from. */
export const Disabled: Story = {
  render: () => (
    <Field data-disabled="true" aria-disabled="true" className="max-w-sm">
      <FieldLabel htmlFor="disabled-email">E-mail address</FieldLabel>
      <Input
        id="disabled-email"
        type="email"
        disabled
        aria-describedby="disabled-email-why"
        defaultValue="camille.martin@example.org"
      />
      <FieldDescription id="disabled-email-why">
        The address you signed in with. Change it in your Account.
      </FieldDescription>
    </Field>
  ),
};
