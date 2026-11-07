import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState, type FormEvent } from "react";

import { AdvancedSection } from "@/components/blocks/advanced-section";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Field,
  FieldDescription,
  FieldError,
  FieldGroup,
  FieldLabel,
  FieldTitle,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";

/** The options of a search that most people leave as they are. */
function SearchExtras({ prefix }: { prefix: string }) {
  return (
    <>
      <Field>
        <FieldLabel htmlFor={`${prefix}-industries`}>Industries</FieldLabel>
        <Input
          id={`${prefix}-industries`}
          aria-describedby={`${prefix}-industries-text`}
          placeholder="For example: fintech, gaming"
        />
        <FieldDescription id={`${prefix}-industries-text`}>
          Matched as whole words in the posting.
        </FieldDescription>
      </Field>
      <Field>
        <FieldTitle id={`${prefix}-company-types`}>
          Company types (optional)
        </FieldTitle>
        <ToggleGroup
          type="multiple"
          variant="outline"
          spacing={2}
          className="flex-wrap"
          aria-labelledby={`${prefix}-company-types`}
        >
          <ToggleGroupItem value="startup">Startup</ToggleGroupItem>
          <ToggleGroupItem value="scaleup">Scale-up</ToggleGroupItem>
          <ToggleGroupItem value="large">Large company</ToggleGroupItem>
          <ToggleGroupItem value="public">Public sector</ToggleGroupItem>
        </ToggleGroup>
      </Field>
    </>
  );
}

/** Held open from outside, as a page does when it opens the section for a reason of its own. */
function HeldOpen() {
  const [open, setOpen] = useState(true);
  return (
    <AdvancedSection open={open} onOpenChange={setOpen}>
      <SearchExtras prefix="open" />
    </AdvancedSection>
  );
}

/** A form whose folded field is wrong: Save opens the section and puts the focus on that field. */
function FoldedError() {
  const [error, setError] = useState(false);
  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    setError(true);
  };
  return (
    <form
      noValidate
      aria-label="Search settings"
      className="flex max-w-md flex-col gap-6"
      onSubmit={onSubmit}
    >
      <Field>
        <FieldLabel htmlFor="error-titles">Job titles</FieldLabel>
        <Input id="error-titles" defaultValue="Product designer" />
      </Field>
      <AdvancedSection
        keepMounted
        errorFieldId={error ? "error-industries" : undefined}
      >
        <Field data-invalid={error ? "true" : undefined}>
          <FieldLabel htmlFor="error-industries">Industries</FieldLabel>
          <Input
            id="error-industries"
            defaultValue="fintech; gaming"
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? "error-industries-message" : undefined}
          />
          {error ? (
            <FieldError id="error-industries-message">
              Separate industries with commas, like fintech, gaming.
            </FieldError>
          ) : null}
        </Field>
      </AdvancedSection>
      <Button type="submit" className="self-start">
        Save search settings
      </Button>
    </form>
  );
}

const meta = {
  title: "Components/Forms/Advanced section",
  component: AdvancedSection,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Folds away the options most people leave as they are, under one link button, so a page shows first what everyone fills in. Every page with such options uses it, so they open and read the same way. Give it a pageKey to remember the open state between visits.",
      },
    },
  },
  args: { children: <SearchExtras prefix="typical" /> },
} satisfies Meta<typeof AdvancedSection>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Folded under the fields everyone fills in. Click Advanced to open it. */
export const Typical: Story = {
  render: (args) => (
    <FieldGroup className="max-w-md">
      <Field>
        <FieldLabel htmlFor="typical-titles">Job titles</FieldLabel>
        <Input id="typical-titles" defaultValue="Product designer" />
      </Field>
      <Field>
        <FieldLabel htmlFor="typical-places">Places</FieldLabel>
        <Input id="typical-places" defaultValue="Nantes, Rennes" />
      </Field>
      <AdvancedSection {...args} />
    </FieldGroup>
  ),
};

/** Open: the chevron turns over and the options follow the button, spaced like the fields above. */
export const Open: Story = {
  render: () => (
    <div className="max-w-md">
      <HeldOpen />
    </div>
  ),
};

/** The title names what is folded when Advanced would say too little, as on a posting page. */
export const CustomTitle: Story = {
  render: () => (
    <div className="max-w-md">
      <AdvancedSection title="More about this posting">
        <div className="flex flex-wrap gap-2">
          <Badge variant="outline">Permanent</Badge>
          <Badge variant="outline">Hybrid</Badge>
          <Badge variant="outline">Nantes</Badge>
        </div>
        <p className="text-sm text-muted-foreground">
          Atelier Brume designs public services for cities in the west of
          France. The team of twelve works from Nantes two days a week.
        </p>
      </AdvancedSection>
    </div>
  ),
};

/** A folded field with an error opens the section and takes the focus; the section cannot close until the error is fixed. This story presses Save itself (play). */
export const Error: Story = {
  render: () => <FoldedError />,
  play: ({ canvasElement }) => {
    canvasElement
      .querySelector<HTMLButtonElement>('button[type="submit"]')
      ?.click();
  },
};

/** A long title wraps under its chevron on a phone; the button stays one target. */
export const LongTitle: Story = {
  render: () => (
    <div className="max-w-xs">
      <AdvancedSection title="Options most people leave as they are: industries, company types and unwanted places">
        <SearchExtras prefix="long" />
      </AdvancedSection>
    </div>
  ),
};
