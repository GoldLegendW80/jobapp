import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";

import { FiltersToolbar } from "@/components/shared/jobs/filters/filters-toolbar";
import {
  FILTERS_SUMMARY,
  SOME_FILTERS,
} from "@/design-system/fixtures/job-filters";
import { WithFilters } from "@/design-system/fixtures/job-harnesses";
import {
  EMPTY_FILTERS,
  type JobFilters,
} from "@/features/jobs/helpers/filters";

const meta = {
  title: "Shared/Jobs/Filters toolbar",
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The toolbar of both Jobs views, List and Map: the search box, Filters and its panel, the status toggles with their counts, and the active filters as chips.",
      },
      story: { inline: false, height: "640px" },
    },
  },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** The toolbar over `initial`; the counts answered, loading, or unavailable. */
function Toolbar({
  initial = EMPTY_FILTERS,
  counts = "answered",
}: {
  initial?: JobFilters;
  counts?: "answered" | "loading" | "unavailable";
}) {
  return (
    <WithFilters initial={initial}>
      {(props) => (
        <FiltersToolbar
          {...props}
          summary={counts === "answered" ? FILTERS_SUMMARY : undefined}
          unavailable={counts === "unavailable"}
        />
      )}
    </WithFilters>
  );
}

/** No filter set: every status with its count. */
export const NoFilter: Story = {
  name: "No filter set",
  render: () => <Toolbar />,
};

/** Filters set: the button counts those of its panel, the chips list them all. */
export const FiltersSet: Story = {
  name: "Filters set",
  render: () => <Toolbar initial={SOME_FILTERS} />,
};

/** Filters opens its panel: every change applies at once, its last button closes it. */
export const OpenTheFilters: Story = {
  name: "Open the filters",
  render: () => (
    <Toolbar initial={{ ...EMPTY_FILTERS, fitMin: 70, fitMax: 79 }} />
  ),
  play: async ({ canvasElement }) => {
    await userEvent.click(
      await within(canvasElement).findByRole("button", { name: /^Filters/ }),
    );
  },
};

/** While the first answer loads, the counts pulse in their place. */
export const CountsLoading: Story = {
  name: "Counts loading",
  render: () => <Toolbar counts="loading" />,
};

/** When the list could not be loaded, the toggles show no count and still filter. */
export const CountsUnavailable: Story = {
  name: "Counts unavailable",
  render: () => <Toolbar counts="unavailable" />,
};

/** On a phone the search and Filters share a row; the statuses scroll sideways below. */
export const OnAPhone: Story = {
  name: "On a phone",
  render: () => (
    <div className="max-w-sm overflow-hidden px-4">
      <Toolbar initial={{ ...EMPTY_FILTERS, statuses: ["shortlisted"] }} />
    </div>
  ),
};
