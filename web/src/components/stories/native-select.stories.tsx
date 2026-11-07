import type { Meta, StoryObj } from "@storybook/react-vite";

import { previewState, StateMatrix } from "@/design-system/state-matrix";

import {
  Field,
  FieldDescription,
  FieldError,
  FieldLabel,
} from "@/components/ui/field";
import { NativeSelect } from "@/components/ui/native-select";

const NOTICE_PERIODS = (
  <>
    <option value="0">None, I can start now</option>
    <option value="1">1 month</option>
    <option value="2">2 months</option>
    <option value="3">3 months</option>
  </>
);

const meta = {
  title: "Components/Inputs/Native select",
  component: NativeSelect,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "One answer from a list too long to show at once, or one the person picks from a known set: a notice period, a work authorisation, a language. On a phone it opens the system picker. Up to seven short options, all worth seeing, go in a ToggleGroup instead.",
      },
    },
  },
  args: { children: NOTICE_PERIODS, defaultValue: "2" },
} satisfies Meta<typeof NativeSelect>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The notice period of a profile. */
export const Typical: Story = {
  render: (args) => (
    <Field className="max-w-xs">
      <FieldLabel htmlFor="typical-notice">Notice period</FieldLabel>
      <NativeSelect id="typical-notice" {...args} />
    </Field>
  ),
};

/** Every state, pinned. */
export const States: Story = {
  render: () => (
    <StateMatrix
      rows={["chosen"] as const}
      states={["default", "focus", "invalid", "disabled"]}
      render={(_, state) => (
        <div className="w-48">
          <NativeSelect
            aria-label={`Notice period, ${state}`}
            defaultValue="2"
            {...(state === "invalid"
              ? { "aria-invalid": true }
              : previewState(state))}
          >
            {NOTICE_PERIODS}
          </NativeSelect>
        </div>
      )}
    />
  ),
};

/** With no sensible default, the first option asks for a choice and cannot be chosen back. */
export const Empty: Story = {
  render: () => (
    <Field className="max-w-xs">
      <FieldLabel htmlFor="empty-authorisation">Work authorisation</FieldLabel>
      <NativeSelect id="empty-authorisation" defaultValue="">
        <option value="" disabled>
          Choose one
        </option>
        <option value="eu">Citizen of the European Union</option>
        <option value="permit">Work permit for France</option>
        <option value="sponsor">I need a visa sponsor</option>
      </NativeSelect>
    </Field>
  ),
};

/** Related options sit under an optgroup heading. */
export const Groups: Story = {
  render: () => (
    <Field className="max-w-xs">
      <FieldLabel htmlFor="groups-language">
        Language of your CV and letters
      </FieldLabel>
      <NativeSelect id="groups-language" defaultValue="auto">
        <option value="auto">Automatic, from the posting</option>
        <optgroup label="Always in">
          <option value="en">English</option>
          <option value="fr">French</option>
        </optgroup>
      </NativeSelect>
    </Field>
  ),
};

/** A long option is cut by the box; the open list shows it whole. Keep options short. */
export const LongText: Story = {
  render: () => (
    <Field className="max-w-48">
      <FieldLabel htmlFor="long-mode">When you click Apply</FieldLabel>
      <NativeSelect id="long-mode" defaultValue="review">
        <option value="one-click">The app fills the form and sends it</option>
        <option value="review">
          Let me review each filled form before it is sent
        </option>
      </NativeSelect>
    </Field>
  ),
};

/** The error says what to choose, under the select. */
export const Error: Story = {
  render: () => (
    <Field data-invalid="true" className="max-w-xs">
      <FieldLabel htmlFor="error-authorisation">Work authorisation</FieldLabel>
      <NativeSelect
        id="error-authorisation"
        defaultValue=""
        aria-invalid="true"
        aria-describedby="error-authorisation-message"
      >
        <option value="" disabled>
          Choose one
        </option>
        <option value="eu">Citizen of the European Union</option>
        <option value="permit">Work permit for France</option>
        <option value="sponsor">I need a visa sponsor</option>
      </NativeSelect>
      <FieldError id="error-authorisation-message">
        Lumen Freight asks for it. Choose the one that fits you.
      </FieldError>
    </Field>
  ),
};

/** A choice the person cannot change here, with a sentence that says why. */
export const Disabled: Story = {
  render: () => (
    <Field data-disabled="true" aria-disabled="true" className="max-w-xs">
      <FieldLabel htmlFor="disabled-notice">Notice period</FieldLabel>
      <NativeSelect
        id="disabled-notice"
        disabled
        defaultValue="2"
        aria-describedby="disabled-notice-why"
      >
        {NOTICE_PERIODS}
      </NativeSelect>
      <FieldDescription id="disabled-notice-why">
        Read from your CV. Change it in your profile.
      </FieldDescription>
    </Field>
  ),
};
