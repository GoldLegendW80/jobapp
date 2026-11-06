import type { Meta, StoryObj } from "@storybook/react-vite";
import { CopyIcon, EyeIcon, LinkIcon, SearchIcon } from "lucide-react";

import { StateMatrix } from "@/design-system/state-matrix";

import {
  Field,
  FieldDescription,
  FieldError,
  FieldGroup,
  FieldLabel,
} from "@/components/ui/field";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
  InputGroupText,
  InputGroupTextarea,
} from "@/components/ui/input-group";
import { Kbd } from "@/components/ui/kbd";

const meta = {
  title: "Components/Inputs/Input group",
  component: InputGroup,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "An input with something attached inside its border: a unit, an icon that says what to type, a button that acts on the value (show, copy). Use InputGroupInput or InputGroupTextarea inside, never a plain Input.",
      },
    },
  },
} satisfies Meta<typeof InputGroup>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A desired salary, with its currency before and its period after. */
export const Typical: Story = {
  render: () => (
    <Field className="max-w-xs">
      <FieldLabel htmlFor="typical-salary">Desired salary</FieldLabel>
      <InputGroup>
        <InputGroupAddon>
          <InputGroupText>€</InputGroupText>
        </InputGroupAddon>
        <InputGroupInput
          id="typical-salary"
          inputMode="numeric"
          autoComplete="off"
          defaultValue="52000"
        />
        <InputGroupAddon align="inline-end">
          <InputGroupText>a year</InputGroupText>
        </InputGroupAddon>
      </InputGroup>
    </Field>
  ),
};

/** Where the addon sits. Inline start for what to type, inline end for a unit or an action, block end under a textarea for a count and its action. */
export const Variants: Story = {
  render: () => (
    <FieldGroup className="max-w-md">
      <Field>
        <FieldLabel htmlFor="variants-search">Search postings</FieldLabel>
        <InputGroup>
          <InputGroupInput
            id="variants-search"
            type="search"
            placeholder="Title, company or skill"
          />
          <InputGroupAddon>
            <SearchIcon />
          </InputGroupAddon>
        </InputGroup>
      </Field>
      <Field>
        <FieldLabel htmlFor="variants-rate">Daily rate (freelance)</FieldLabel>
        <InputGroup className="max-w-56">
          <InputGroupInput
            id="variants-rate"
            inputMode="numeric"
            defaultValue="550"
          />
          <InputGroupAddon align="inline-end">
            <InputGroupText>€ a day</InputGroupText>
          </InputGroupAddon>
        </InputGroup>
      </Field>
      <Field>
        <FieldLabel htmlFor="variants-answer">
          Why do you want to join Lumen Freight?
        </FieldLabel>
        <InputGroup>
          <InputGroupTextarea
            id="variants-answer"
            defaultValue="Your routing tools are used by small carriers I worked with for three years, and I want to make them easier to learn."
          />
          <InputGroupAddon align="block-end">
            <InputGroupText>118 of 1,000 characters</InputGroupText>
            <InputGroupButton variant="secondary" className="ms-auto">
              Write with the assistant
            </InputGroupButton>
          </InputGroupAddon>
        </InputGroup>
      </Field>
    </FieldGroup>
  ),
};

/** An icon leads to say what goes in; an icon button trails to act on the value, with an accessible name. A key hint fits at the end. */
export const WithIcon: Story = {
  render: () => (
    <FieldGroup className="max-w-md">
      <Field>
        <FieldLabel htmlFor="icon-posting">Posting link</FieldLabel>
        <InputGroup>
          <InputGroupInput
            id="icon-posting"
            type="url"
            placeholder="Paste the address of a posting"
          />
          <InputGroupAddon>
            <LinkIcon />
          </InputGroupAddon>
        </InputGroup>
      </Field>
      <Field>
        <FieldLabel htmlFor="icon-key">API key</FieldLabel>
        <InputGroup>
          <InputGroupInput
            id="icon-key"
            type="password"
            autoComplete="off"
            className="font-mono"
            defaultValue="sk-ant-api03-example"
          />
          <InputGroupAddon align="inline-end">
            <InputGroupButton
              size="icon-xs"
              aria-label="Show API key"
              aria-pressed={false}
            >
              <EyeIcon />
            </InputGroupButton>
          </InputGroupAddon>
        </InputGroup>
      </Field>
      <Field>
        <FieldLabel htmlFor="icon-filter">Filter the jobs list</FieldLabel>
        <InputGroup>
          <InputGroupInput id="icon-filter" type="search" />
          <InputGroupAddon>
            <SearchIcon />
          </InputGroupAddon>
          <InputGroupAddon align="inline-end">
            <Kbd>/</Kbd>
          </InputGroupAddon>
        </InputGroup>
      </Field>
    </FieldGroup>
  ),
};

/** Every state, pinned. Focus is drawn by the group when its input has focus, so this story focuses that input itself (play). Disabled takes data-disabled and aria-disabled on the group, disabled on the input. */
export const States: Story = {
  render: () => (
    <StateMatrix
      rows={["salary"] as const}
      states={["default", "focus", "invalid", "disabled"]}
      render={(_, state) => (
        <InputGroup
          className="w-44"
          {...(state === "disabled"
            ? { "data-disabled": "true", "aria-disabled": true }
            : {})}
        >
          <InputGroupAddon>
            <InputGroupText>€</InputGroupText>
          </InputGroupAddon>
          <InputGroupInput
            id={`states-${state}`}
            aria-label={`Desired salary, ${state}`}
            inputMode="numeric"
            defaultValue="52000"
            aria-invalid={state === "invalid" ? true : undefined}
            disabled={state === "disabled"}
          />
          <InputGroupAddon align="inline-end">
            <InputGroupText>a year</InputGroupText>
          </InputGroupAddon>
        </InputGroup>
      )}
    />
  ),
  play: ({ canvasElement }) => {
    canvasElement.querySelector<HTMLInputElement>("#states-focus")?.focus();
  },
};

/** A long value scrolls inside the input; the addons keep their place. The invite link is copied by its button. */
export const LongText: Story = {
  render: () => (
    <Field className="max-w-sm">
      <FieldLabel htmlFor="long-invite">Invite link</FieldLabel>
      <InputGroup>
        <InputGroupInput
          id="long-invite"
          readOnly
          className="font-mono"
          defaultValue="https://jobapp.example.org/invite/7f3c9a1e2b4d4c8e9a0f5d6b1c2e3f4a"
        />
        <InputGroupAddon align="inline-end">
          <InputGroupButton>
            <CopyIcon />
            Copy
          </InputGroupButton>
        </InputGroupAddon>
      </InputGroup>
      <FieldDescription>
        Send this link to the person you invite.
      </FieldDescription>
    </Field>
  ),
};

/** The error sits under the group, and the whole border turns red from the input's aria-invalid. */
export const Error: Story = {
  render: () => (
    <Field data-invalid="true" className="max-w-sm">
      <FieldLabel htmlFor="error-key">API key</FieldLabel>
      <InputGroup>
        <InputGroupInput
          id="error-key"
          className="font-mono"
          aria-invalid="true"
          aria-describedby="error-key-message"
          defaultValue="sk-proj-example"
        />
        <InputGroupAddon align="inline-end">
          <InputGroupButton size="icon-xs" aria-label="Show API key">
            <EyeIcon />
          </InputGroupButton>
        </InputGroupAddon>
      </InputGroup>
      <FieldError id="error-key-message">
        This is not an Anthropic key: it starts with sk-ant-.
      </FieldError>
    </Field>
  ),
};
