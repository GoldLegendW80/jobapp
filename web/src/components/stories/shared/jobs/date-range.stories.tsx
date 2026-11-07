import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";

import { DateRange } from "@/components/shared/jobs/filters/date-range";
import { WithFilters } from "@/design-system/fixtures/job-harnesses";
import { EMPTY_FILTERS } from "@/features/jobs/helpers/filters";

const meta = {
  title: "Shared/Jobs/Date range",
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The postings found between two dates, typed or picked on a calendar (a Popover with a range Calendar), applied as soon as the pair is valid: the last field of the Filters panel in the Jobs toolbar.",
      },
    },
  },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** No date: every posting, whenever it was found. */
export const NoDates: Story = {
  name: "No dates",
  render: () => (
    <WithFilters>{(props) => <DateRange {...props} />}</WithFilters>
  ),
};

/** The postings of October. */
export const RangeSet: Story = {
  name: "A range set",
  render: () => (
    <WithFilters
      initial={{
        ...EMPTY_FILTERS,
        foundFrom: "2026-10-01",
        foundTo: "2026-10-31",
      }}
    >
      {(props) => <DateRange {...props} />}
    </WithFilters>
  ),
};

/** A first date after the second is not applied, and says why. */
export const WrongWayRound: Story = {
  name: "The first date after the second",
  render: RangeSet.render,
  play: async ({ canvasElement }) => {
    const from = within(canvasElement).getByLabelText("Found from");
    await userEvent.clear(from);
    await userEvent.type(from, "15 Nov 2026{Enter}");
    await within(canvasElement).findByText(
      "The first date is after the second.",
    );
  },
};

/** The calendar beside the fields: the first day picked, then the other end. */
export const PickOnACalendar: Story = {
  name: "Pick the dates on a calendar",
  render: RangeSet.render,
  parameters: { docs: { story: { inline: false, height: "480px" } } },
  play: async ({ canvasElement }) => {
    await userEvent.click(
      within(canvasElement).getByRole("button", {
        name: "Pick the dates on a calendar",
      }),
    );
  },
};

/** At a phone's width, in the panel: the two fields and the calendar button on one row. */
export const OnAPhone: Story = {
  name: "On a phone",
  render: () => (
    <div className="max-w-sm">
      <WithFilters>{(props) => <DateRange {...props} />}</WithFilters>
    </div>
  ),
};
