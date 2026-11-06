import type { Meta, StoryObj } from "@storybook/react-vite";

import { previewState, StateMatrix } from "@/design-system/state-matrix";

import {
  Field,
  FieldContent,
  FieldDescription,
  FieldError,
  FieldLabel,
  FieldLegend,
  FieldSet,
  FieldTitle,
} from "@/components/ui/field";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";

const meta = {
  title: "Components/Inputs/Radio group",
  component: RadioGroup,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "One answer among a few, when each needs a sentence to be understood: how the app applies, which CV a posting starts from. Short options without a sentence go in a ToggleGroup, long lists in a NativeSelect.",
      },
    },
  },
  args: { defaultValue: "review" },
} satisfies Meta<typeof RadioGroup>;

export default meta;
type Story = StoryObj<typeof meta>;

/** How the app applies after a Like, each option with the sentence that explains it. */
export const Typical: Story = {
  render: (args) => (
    <FieldSet className="max-w-md">
      <FieldLegend variant="label">After a Like</FieldLegend>
      <RadioGroup {...args}>
        <Field orientation="horizontal">
          <RadioGroupItem
            id="typical-review"
            value="review"
            aria-describedby="typical-review-text"
          />
          <FieldContent>
            <FieldLabel htmlFor="typical-review">Review first</FieldLabel>
            <FieldDescription id="typical-review-text">
              The app fills the form and waits for your approval before it
              submits.
            </FieldDescription>
          </FieldContent>
        </Field>
        <Field orientation="horizontal">
          <RadioGroupItem
            id="typical-full"
            value="full_auto"
            aria-describedby="typical-full-text"
          />
          <FieldContent>
            <FieldLabel htmlFor="typical-full">Full auto</FieldLabel>
            <FieldDescription id="typical-full-text">
              Your Like is your approval: the app fills the form, checks it once
              more, then submits it for you.
            </FieldDescription>
          </FieldContent>
        </Field>
      </RadioGroup>
    </FieldSet>
  ),
};

/** After options with a sentence each (Typical): plain labels when the words say it all, then choice cards when the option is the main decision of the page. A card names its radio by its title (aria-labelledby). */
export const Variants: Story = {
  render: () => (
    <div className="flex max-w-md flex-col gap-8">
      <FieldSet>
        <FieldLegend variant="label">Tone</FieldLegend>
        <RadioGroup defaultValue="plain">
          {(
            [
              ["plain", "Plain"],
              ["formal", "Formal"],
              ["warm", "Warm"],
            ] as const
          ).map(([value, label]) => (
            <Field key={value} orientation="horizontal">
              <RadioGroupItem id={`plain-${value}`} value={value} />
              <FieldLabel htmlFor={`plain-${value}`} className="font-normal">
                {label}
              </FieldLabel>
            </Field>
          ))}
        </RadioGroup>
      </FieldSet>
      <FieldSet>
        <FieldLegend variant="label">
          Language of your CV and letters
        </FieldLegend>
        <RadioGroup defaultValue="auto">
          {(
            [
              [
                "auto",
                "Automatic, from the posting",
                "Written in the language the posting uses.",
              ],
              ["en", "English", "Every document in English."],
              ["fr", "French", "Every document in French."],
            ] as const
          ).map(([value, title, text]) => (
            <FieldLabel key={value} htmlFor={`card-${value}`}>
              <Field orientation="horizontal">
                <FieldContent>
                  <FieldTitle id={`card-${value}-title`}>{title}</FieldTitle>
                  <FieldDescription id={`card-${value}-text`}>
                    {text}
                  </FieldDescription>
                </FieldContent>
                <RadioGroupItem
                  id={`card-${value}`}
                  value={value}
                  aria-labelledby={`card-${value}-title`}
                  aria-describedby={`card-${value}-text`}
                />
              </Field>
            </FieldLabel>
          ))}
        </RadioGroup>
      </FieldSet>
    </div>
  ),
};

/** Unchosen and chosen, in every state, pinned. */
export const States: Story = {
  render: () => (
    <StateMatrix
      rows={["unchecked", "checked"] as const}
      states={["default", "focus", "invalid", "disabled"]}
      render={(row, state) => (
        <RadioGroup
          aria-label={`Tone, ${row}, ${state}`}
          defaultValue={row === "checked" ? "plain" : undefined}
        >
          <RadioGroupItem
            value="plain"
            aria-label="Plain"
            {...(state === "invalid"
              ? { "aria-invalid": true }
              : previewState(state))}
          />
        </RadioGroup>
      )}
    />
  ),
};

/** Nothing is chosen until the person picks: the form asks, then says what is missing. */
export const Error: Story = {
  render: () => (
    <FieldSet data-invalid="true" className="max-w-md">
      <FieldLegend variant="label">
        Have you worked for Lumen Freight before?
      </FieldLegend>
      <RadioGroup aria-describedby="error-before-message">
        {(
          [
            ["yes", "Yes"],
            ["no", "No"],
          ] as const
        ).map(([value, label]) => (
          <Field key={value} orientation="horizontal" data-invalid="true">
            <RadioGroupItem
              id={`error-${value}`}
              value={value}
              aria-invalid="true"
            />
            <FieldLabel htmlFor={`error-${value}`} className="font-normal">
              {label}
            </FieldLabel>
          </Field>
        ))}
      </RadioGroup>
      <FieldError id="error-before-message">
        The form asks for an answer. Choose yes or no.
      </FieldError>
    </FieldSet>
  ),
};

/** One option that cannot be chosen yet stays in the list, disabled, with a sentence that says why. */
export const Disabled: Story = {
  render: () => (
    <FieldSet className="max-w-md">
      <FieldLegend variant="label">After a Like</FieldLegend>
      <RadioGroup defaultValue="review">
        <Field orientation="horizontal">
          <RadioGroupItem id="disabled-review" value="review" />
          <FieldLabel htmlFor="disabled-review">Review first</FieldLabel>
        </Field>
        <Field
          orientation="horizontal"
          data-disabled="true"
          aria-disabled="true"
        >
          <RadioGroupItem
            id="disabled-full"
            value="full_auto"
            disabled
            aria-describedby="disabled-full-why"
          />
          <FieldContent>
            <FieldLabel htmlFor="disabled-full">Full auto</FieldLabel>
            <FieldDescription id="disabled-full-why">
              Needs a model key. Add one in your Account.
            </FieldDescription>
          </FieldContent>
        </Field>
      </RadioGroup>
    </FieldSet>
  ),
};
