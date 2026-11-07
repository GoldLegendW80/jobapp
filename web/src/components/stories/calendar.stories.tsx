import type { Meta, StoryObj } from "@storybook/react-vite";
import { format } from "date-fns";
import { CalendarIcon } from "lucide-react";
import { useState } from "react";
import type { DateRange } from "react-day-picker";

import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";

/** A fixed day, so the stories look the same every day they are opened. */
const TODAY = new Date(2026, 10, 6);
const DAY = (day: number) => new Date(2026, 10, day);

function rangeLabel(range: DateRange | undefined): string {
  if (!range?.from) return "Any date";
  if (!range.to) return `From ${format(range.from, "d MMM yyyy")}`;
  return `${format(range.from, "d MMM")} to ${format(range.to, "d MMM yyyy")}`;
}

/** The Found between filter of the jobs list: a button that says the range, a calendar under it. */
function FoundBetween({
  defaultOpen = false,
  initial = { from: DAY(2), to: DAY(6) },
}: {
  defaultOpen?: boolean;
  initial?: DateRange | undefined;
}) {
  const [range, setRange] = useState<DateRange | undefined>(initial);
  return (
    <Field className="w-fit">
      <FieldLabel htmlFor="found-between">Found between</FieldLabel>
      <Popover defaultOpen={defaultOpen}>
        <PopoverTrigger asChild>
          <Button
            id="found-between"
            variant="outline"
            className="w-64 justify-start font-normal"
          >
            <CalendarIcon />
            {rangeLabel(range)}
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-auto p-0" align="start">
          <Calendar
            mode="range"
            selected={range}
            onSelect={setRange}
            defaultMonth={range?.from ?? TODAY}
            today={TODAY}
            disabled={{ after: TODAY }}
            weekStartsOn={1}
          />
        </PopoverContent>
      </Popover>
      <FieldDescription>The days the app found the postings.</FieldDescription>
    </Field>
  );
}

const meta = {
  title: "Components/Inputs/Calendar",
  component: Calendar,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Days to pick from a month: a single day, or a range. In a form it opens from a button in a Popover (a date picker), which says the chosen dates in words. Weeks start on Monday; days the person cannot pick (after today, for when postings were found) are disabled.",
      },
    },
  },
} satisfies Meta<typeof Calendar>;

export default meta;
type Story = StoryObj<typeof meta>;

/** One day, today marked. */
export const Typical: Story = {
  render: () => (
    <Calendar
      mode="single"
      defaultMonth={TODAY}
      today={TODAY}
      selected={DAY(4)}
      weekStartsOn={1}
      className="rounded-xl"
    />
  ),
};

/** A range: its ends are pills, the days between are a band. */
export const Range: Story = {
  render: () => (
    <Calendar
      mode="range"
      defaultMonth={TODAY}
      today={TODAY}
      selected={{ from: DAY(2), to: DAY(6) }}
      weekStartsOn={1}
      className="rounded-xl"
    />
  ),
};

/** The date picker of the jobs list's filters: Found between, a range in a popover. Click it. */
export const DatePicker: Story = {
  render: () => <FoundBetween />,
};

/** Open, for review. */
export const DatePickerOpen: Story = {
  render: () => <FoundBetween defaultOpen />,
  parameters: { docs: { story: { inline: false, height: "480px" } } },
};

/** Nothing picked yet: the button says Any date. */
export const NoDate: Story = {
  render: () => <FoundBetween initial={undefined} />,
};

/** Two months side by side, for a range across a month's end, on a wide screen. */
export const TwoMonths: Story = {
  render: () => (
    <Calendar
      mode="range"
      numberOfMonths={2}
      defaultMonth={new Date(2026, 9, 1)}
      today={TODAY}
      selected={{ from: new Date(2026, 9, 27), to: DAY(3) }}
      weekStartsOn={1}
      className="rounded-xl"
    />
  ),
};

/** A month and a year to pick from lists, for a date far back: a diploma, a first job. */
export const WithDropdowns: Story = {
  render: () => (
    <Calendar
      mode="single"
      captionLayout="dropdown"
      defaultMonth={new Date(2017, 8, 1)}
      startMonth={new Date(1990, 0, 1)}
      endMonth={TODAY}
      weekStartsOn={1}
      className="rounded-xl"
    />
  ),
};
