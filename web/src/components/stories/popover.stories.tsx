import type { Meta, StoryObj } from "@storybook/react-vite";
import { InfoIcon, SlidersHorizontalIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  Popover,
  PopoverContent,
  PopoverDescription,
  PopoverHeader,
  PopoverTitle,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

// Shown in its own iframe on the docs page, so the open popover has room.
const OPEN_FOR_REVIEW = {
  docs: { story: { inline: false, height: "360px" } },
} as const;

const meta = {
  title: "Components/Overlays/Popover",
  component: Popover,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "A small panel anchored to the button that opened it, for a short choice beside the work: a date range, a fit range, why a score is what it is. It closes on Escape or a click outside and gives the focus back. A task with several steps is a Dialog; a menu of actions is a DropdownMenu.",
      },
    },
  },
} satisfies Meta<typeof Popover>;

export default meta;
type Story = StoryObj<typeof meta>;

function FitRange({ defaultOpen = false }: { defaultOpen?: boolean }) {
  return (
    <Popover defaultOpen={defaultOpen}>
      <PopoverTrigger asChild>
        <Button variant="outline">
          <SlidersHorizontalIcon />
          Fit: 70 to 100
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="flex flex-col gap-4">
        <PopoverHeader>
          <PopoverTitle>Fit range</PopoverTitle>
          <PopoverDescription>
            Show the postings whose fit is in this range.
          </PopoverDescription>
        </PopoverHeader>
        <FieldGroup className="grid grid-cols-2 gap-3">
          <Field>
            <FieldLabel htmlFor="fit-min">From</FieldLabel>
            <Input id="fit-min" type="number" defaultValue={70} />
          </Field>
          <Field>
            <FieldLabel htmlFor="fit-max">To</FieldLabel>
            <Input id="fit-max" type="number" defaultValue={100} />
          </Field>
        </FieldGroup>
      </PopoverContent>
    </Popover>
  );
}

/** A filter of the jobs list: the fit range, set beside the list. Click to open. */
export const Typical: Story = {
  render: () => <FitRange />,
};

/** Open, for review. */
export const Open: Story = {
  render: () => <FitRange defaultOpen />,
  parameters: OPEN_FOR_REVIEW,
};

/** An explanation the person opens on purpose: longer than a tooltip, and it can hold a link. */
export const Explanation: Story = {
  render: () => (
    <Popover defaultOpen>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon-sm" aria-label="Why fit 86">
          <InfoIcon />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="flex flex-col gap-2 text-sm">
        <PopoverTitle>Why fit 86</PopoverTitle>
        <p className="text-muted-foreground">
          You have 7 of the 9 skills it asks for. The pay is in your range and
          Lyon is one of your places. Kafka and Spark are missing.
        </p>
        <a
          href="#assessment"
          className="w-fit font-medium underline underline-offset-4"
        >
          Read the assessment
        </a>
      </PopoverContent>
    </Popover>
  ),
  parameters: OPEN_FOR_REVIEW,
};

/** A trigger with a tooltip too: the trigger passes its ref on, so both anchor to it. */
export const WithTooltip: Story = {
  render: () => (
    <Popover>
      <Tooltip>
        <TooltipTrigger asChild>
          <PopoverTrigger asChild>
            <Button variant="outline" size="icon" aria-label="Filters">
              <SlidersHorizontalIcon />
            </Button>
          </PopoverTrigger>
        </TooltipTrigger>
        <TooltipContent>Filters</TooltipContent>
      </Tooltip>
      <PopoverContent>Filters of the jobs list.</PopoverContent>
    </Popover>
  ),
};
