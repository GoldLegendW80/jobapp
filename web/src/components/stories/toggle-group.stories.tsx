import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  Building2Icon,
  HouseIcon,
  LayoutGridIcon,
  ListIcon,
  ShuffleIcon,
} from "lucide-react";

import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldTitle,
} from "@/components/ui/field";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";

const meta = {
  title: "Components/Actions/Toggle group",
  component: ToggleGroup,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "A choice among two to seven short options, all in view: work mode, contract types, a view of a list. Single for one answer, multiple when the person can pick several. More options, or options that need a sentence each, go in a RadioGroup or a NativeSelect.",
      },
    },
  },
  args: { type: "multiple", variant: "outline", spacing: 2 },
  argTypes: {
    variant: { control: "inline-radio", options: ["outline", "default"] },
    size: { control: "inline-radio", options: ["sm", "default", "lg"] },
    spacing: { control: "inline-radio", options: [0, 1, 2] },
  },
} satisfies Meta<typeof ToggleGroup>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The contract types of a search, several at once, labelled with FieldTitle and aria-labelledby. */
export const Typical: Story = {
  render: ({ variant, size, spacing }) => (
    <Field>
      <FieldTitle id="typical-contracts">Contract types</FieldTitle>
      <ToggleGroup
        type="multiple"
        variant={variant}
        size={size}
        spacing={spacing}
        aria-labelledby="typical-contracts"
        defaultValue={["permanent", "freelance"]}
      >
        <ToggleGroupItem value="permanent">Permanent</ToggleGroupItem>
        <ToggleGroupItem value="fixed-term">Fixed term</ToggleGroupItem>
        <ToggleGroupItem value="freelance">Freelance mission</ToggleGroupItem>
      </ToggleGroup>
    </Field>
  ),
};

/** In priority order. Joined (spacing 0) for one answer, read as a segmented control; spaced for several answers, read as chips; default (borderless) inside a toolbar. */
export const Variants: Story = {
  render: () => (
    <FieldGroup className="max-w-xl">
      <Field>
        <FieldTitle id="variants-work-mode">Work mode</FieldTitle>
        <ToggleGroup
          type="single"
          variant="outline"
          aria-labelledby="variants-work-mode"
          defaultValue="hybrid"
        >
          <ToggleGroupItem value="remote">Remote</ToggleGroupItem>
          <ToggleGroupItem value="hybrid">Hybrid</ToggleGroupItem>
          <ToggleGroupItem value="office">On site</ToggleGroupItem>
        </ToggleGroup>
      </Field>
      <Field>
        <FieldTitle id="variants-contracts">Contract types</FieldTitle>
        <ToggleGroup
          type="multiple"
          variant="outline"
          spacing={2}
          aria-labelledby="variants-contracts"
          defaultValue={["permanent", "fixed-term"]}
        >
          <ToggleGroupItem value="permanent">Permanent</ToggleGroupItem>
          <ToggleGroupItem value="fixed-term">Fixed term</ToggleGroupItem>
          <ToggleGroupItem value="freelance">Freelance mission</ToggleGroupItem>
        </ToggleGroup>
      </Field>
      <Field>
        <FieldTitle id="variants-tone">Tone</FieldTitle>
        <ToggleGroup
          type="single"
          aria-labelledby="variants-tone"
          defaultValue="plain"
        >
          <ToggleGroupItem value="plain">Plain</ToggleGroupItem>
          <ToggleGroupItem value="formal">Formal</ToggleGroupItem>
          <ToggleGroupItem value="warm">Warm</ToggleGroupItem>
        </ToggleGroup>
      </Field>
    </FieldGroup>
  ),
};

/** sm in the toolbar of a table, default in forms, lg on a touch-first page. */
export const Sizes: Story = {
  render: () => (
    <div className="flex flex-col items-start gap-3">
      {(["sm", "default", "lg"] as const).map((size) => (
        <ToggleGroup
          key={size}
          type="single"
          variant="outline"
          size={size}
          aria-label={`Work mode, ${size}`}
          defaultValue="remote"
        >
          <ToggleGroupItem value="remote">Remote</ToggleGroupItem>
          <ToggleGroupItem value="hybrid">Hybrid</ToggleGroupItem>
          <ToggleGroupItem value="office">On site</ToggleGroupItem>
        </ToggleGroup>
      ))}
    </div>
  ),
};

/** An icon leads each label. Icons alone fit a view switch, each with an accessible name. */
export const WithIcon: Story = {
  render: () => (
    <div className="flex flex-wrap items-center gap-6">
      <ToggleGroup
        type="single"
        variant="outline"
        aria-label="Work mode"
        defaultValue="remote"
      >
        <ToggleGroupItem value="remote">
          <HouseIcon />
          Remote
        </ToggleGroupItem>
        <ToggleGroupItem value="hybrid">
          <ShuffleIcon />
          Hybrid
        </ToggleGroupItem>
        <ToggleGroupItem value="office">
          <Building2Icon />
          On site
        </ToggleGroupItem>
      </ToggleGroup>
      <ToggleGroup
        type="single"
        variant="outline"
        aria-label="Show postings as"
        defaultValue="list"
      >
        <ToggleGroupItem value="list" aria-label="A list">
          <ListIcon />
        </ToggleGroupItem>
        <ToggleGroupItem value="cards" aria-label="Cards">
          <LayoutGridIcon />
        </ToggleGroupItem>
      </ToggleGroup>
    </div>
  ),
};

/** Seven options is the most a group holds. Spaced items wrap on a phone, joined items never do: keep joined groups to three short words. */
export const ManyOptions: Story = {
  render: () => (
    <Field className="max-w-xl">
      <FieldTitle id="many-company-types">Company types (optional)</FieldTitle>
      <ToggleGroup
        type="multiple"
        variant="outline"
        spacing={2}
        className="flex-wrap"
        aria-labelledby="many-company-types"
        defaultValue={["startup", "scaleup"]}
      >
        <ToggleGroupItem value="startup">Startup</ToggleGroupItem>
        <ToggleGroupItem value="scaleup">Scale-up</ToggleGroupItem>
        <ToggleGroupItem value="large">Large company</ToggleGroupItem>
        <ToggleGroupItem value="public">Public sector</ToggleGroupItem>
        <ToggleGroupItem value="nonprofit">Nonprofit</ToggleGroupItem>
        <ToggleGroupItem value="agency">Agency</ToggleGroupItem>
        <ToggleGroupItem value="consulting">Consulting firm</ToggleGroupItem>
      </ToggleGroup>
    </Field>
  ),
};

/** A whole group is disabled while it cannot change, with a sentence that says why. */
export const Disabled: Story = {
  render: () => (
    <Field data-disabled="true" aria-disabled="true">
      <FieldTitle id="disabled-work-mode">Work mode</FieldTitle>
      <ToggleGroup
        type="single"
        variant="outline"
        disabled
        aria-labelledby="disabled-work-mode"
        aria-describedby="disabled-work-mode-why"
        defaultValue="remote"
      >
        <ToggleGroupItem value="remote">Remote</ToggleGroupItem>
        <ToggleGroupItem value="hybrid">Hybrid</ToggleGroupItem>
        <ToggleGroupItem value="office">On site</ToggleGroupItem>
      </ToggleGroup>
      <FieldDescription id="disabled-work-mode-why">
        A search is running. You can change this when it ends.
      </FieldDescription>
    </Field>
  ),
};
