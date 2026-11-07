import type { Meta, StoryObj } from "@storybook/react-vite";

import { previewState, StateMatrix } from "@/design-system/state-matrix";

import {
  Field,
  FieldDescription,
  FieldError,
  FieldLabel,
} from "@/components/ui/field";
import { Textarea } from "@/components/ui/textarea";

const meta = {
  title: "Components/Inputs/Textarea",
  component: Textarea,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Text of more than one line: an answer to an application question, the skills you want to use, a message to the assistant. It grows with its text; a count or an action under it makes it an InputGroup.",
      },
    },
  },
  args: {
    placeholder:
      "For example: design systems, user research, accessibility audits",
  },
} satisfies Meta<typeof Textarea>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The skills a person wants to use in their next job, with a description that says how it is read. */
export const Typical: Story = {
  render: (args) => (
    <Field className="max-w-md">
      <FieldLabel htmlFor="typical-skills">Skills you want to use</FieldLabel>
      <Textarea
        id="typical-skills"
        aria-describedby="typical-skills-hint"
        {...args}
      />
      <FieldDescription id="typical-skills-hint">
        Separate them with commas. Postings that ask for them score higher.
      </FieldDescription>
    </Field>
  ),
};

/** Empty and filled, in every state, pinned. */
export const States: Story = {
  render: () => (
    <StateMatrix
      rows={["empty", "filled"] as const}
      states={["default", "focus", "invalid", "disabled"]}
      render={(row, state) => (
        <Textarea
          aria-label={`Skills you want to use, ${row}, ${state}`}
          className="w-48"
          placeholder="For example: user research"
          defaultValue={
            row === "filled" ? "Design systems, user research" : undefined
          }
          {...(state === "invalid"
            ? { "aria-invalid": true }
            : previewState(state))}
        />
      )}
    />
  ),
};

/** The box grows with its text up to a height the page sets (max-h), then scrolls. */
export const LongText: Story = {
  render: () => (
    <Field className="max-w-md">
      <FieldLabel htmlFor="long-answer">
        Why do you want to join Atelier Brume?
      </FieldLabel>
      <Textarea
        id="long-answer"
        className="max-h-48"
        defaultValue={
          "I have followed Atelier Brume since your redesign of the Nantes library catalogue: it made a dense collection easy to browse on a phone, and it stayed readable for people who use a screen reader.\n\nFor the past four years I have led the design system of a logistics company, where I learned to make a small set of components cover many screens without losing their meaning. I would like to bring that care to public services, and to work with a team that tests its work with the people who use it.\n\nI can start in January, after a notice period of eight weeks."
        }
      />
    </Field>
  ),
};

/** The error says what is missing, under the box. */
export const Error: Story = {
  render: () => (
    <Field data-invalid="true" className="max-w-md">
      <FieldLabel htmlFor="error-answer">
        Why do you want to join Lumen Freight?
      </FieldLabel>
      <Textarea
        id="error-answer"
        aria-invalid="true"
        aria-describedby="error-answer-message"
        defaultValue="Good company."
      />
      <FieldError id="error-answer-message">
        The form asks for at least 50 characters. Add a sentence or two.
      </FieldError>
    </Field>
  ),
};

/** Text the person cannot change while the assistant writes it, with a sentence that says so. */
export const Disabled: Story = {
  render: () => (
    <Field data-disabled="true" aria-disabled="true" className="max-w-md">
      <FieldLabel htmlFor="disabled-letter">Cover letter</FieldLabel>
      <Textarea
        id="disabled-letter"
        disabled
        aria-describedby="disabled-letter-why"
        defaultValue="Dear hiring team of Lumen Freight,"
      />
      <FieldDescription id="disabled-letter-why">
        The assistant is writing this letter. You can edit it when it is done.
      </FieldDescription>
    </Field>
  ),
};
