import type { Meta, StoryObj } from "@storybook/react-vite";

import { previewState, StateMatrix } from "@/design-system/state-matrix";

import {
  Field,
  FieldContent,
  FieldDescription,
  FieldGroup,
  FieldLabel,
  FieldSeparator,
} from "@/components/ui/field";
import { Switch } from "@/components/ui/switch";

const meta = {
  title: "Components/Inputs/Switch",
  component: Switch,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "A setting that takes effect the moment it is flipped, with no Save button: Remote only, a daily search, a feed that is on. In a form saved by a button, use a Checkbox. The label says what On does.",
      },
    },
  },
  args: { defaultChecked: true },
  argTypes: { size: { control: "inline-radio", options: ["sm", "default"] } },
} satisfies Meta<typeof Switch>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A search setting: label and sentence on the left, the switch on the right. */
export const Typical: Story = {
  render: (args) => (
    <Field orientation="horizontal" className="max-w-md">
      <FieldContent>
        <FieldLabel htmlFor="typical-remote">Remote only</FieldLabel>
        <FieldDescription id="typical-remote-text">
          Only postings you can do from home reach your deck.
        </FieldDescription>
      </FieldContent>
      <Switch
        id="typical-remote"
        aria-describedby="typical-remote-text"
        {...args}
      />
    </Field>
  ),
};

/** default in settings, sm inside a dense row such as a list of feeds. */
export const Sizes: Story = {
  render: () => (
    <FieldGroup className="max-w-xs">
      <Field orientation="horizontal">
        <FieldLabel htmlFor="size-default">Remote only</FieldLabel>
        <Switch id="size-default" defaultChecked />
      </Field>
      <Field orientation="horizontal">
        <FieldLabel htmlFor="size-sm" className="font-normal">
          We Work Remotely
        </FieldLabel>
        <Switch id="size-sm" size="sm" defaultChecked />
      </Field>
    </FieldGroup>
  ),
};

/** Off and on, in each size, in every state, pinned. A switch has no invalid state: it is never wrong. */
export const States: Story = {
  render: () => (
    <StateMatrix
      rows={["off", "on", "off, sm", "on, sm"] as const}
      states={["default", "focus", "disabled"]}
      render={(row, state) => (
        <Switch
          aria-label={`Remote only, ${row}, ${state}`}
          size={row.endsWith("sm") ? "sm" : "default"}
          defaultChecked={row.startsWith("on")}
          {...previewState(state)}
        />
      )}
    />
  ),
};

/** Settings of one kind stack in a FieldGroup, separated where the subject changes. */
export const SettingsList: Story = {
  render: () => (
    <FieldGroup className="max-w-md">
      <Field orientation="horizontal">
        <FieldContent>
          <FieldLabel htmlFor="list-daily">
            Find new postings every morning
          </FieldLabel>
          <FieldDescription>
            The search runs at 7:00 and your deck is ready when you wake up.
          </FieldDescription>
        </FieldContent>
        <Switch id="list-daily" defaultChecked />
      </Field>
      <Field orientation="horizontal">
        <FieldContent>
          <FieldLabel htmlFor="list-documents">
            Write documents from a fit of 70
          </FieldLabel>
          <FieldDescription>
            A CV and a cover letter wait for every posting above it.
          </FieldDescription>
        </FieldContent>
        <Switch id="list-documents" />
      </Field>
      <FieldSeparator />
      <Field orientation="horizontal">
        <FieldContent>
          <FieldLabel htmlFor="list-mail">Read replies in Gmail</FieldLabel>
          <FieldDescription>
            The app reads answers from employers. It never sends a mail by
            itself.
          </FieldDescription>
        </FieldContent>
        <Switch id="list-mail" defaultChecked />
      </Field>
    </FieldGroup>
  ),
};

/** A label that can wrap goes in a FieldContent, so the switch stays on its first line instead of its middle. */
export const LongText: Story = {
  render: () => (
    <Field orientation="horizontal" className="max-w-xs">
      <FieldContent>
        <FieldLabel htmlFor="long-unwanted">
          Hide postings from companies I already applied to this year
        </FieldLabel>
      </FieldContent>
      <Switch id="long-unwanted" />
    </Field>
  ),
};

/** A setting the person cannot flip yet, with a sentence that says what it needs. */
export const Disabled: Story = {
  render: () => (
    <Field
      orientation="horizontal"
      data-disabled="true"
      aria-disabled="true"
      className="max-w-md"
    >
      <FieldContent>
        <FieldLabel htmlFor="disabled-full">Full auto</FieldLabel>
        <FieldDescription id="disabled-full-why">
          Needs a model key. Add one in your Account.
        </FieldDescription>
      </FieldContent>
      <Switch
        id="disabled-full"
        disabled
        aria-describedby="disabled-full-why"
      />
    </Field>
  ),
};
