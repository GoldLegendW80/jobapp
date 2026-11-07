import type { Meta, StoryObj } from "@storybook/react-vite";

import { StatusFilter } from "@/components/shared/jobs/filters/status-filter";
import { STATUS_COUNTS } from "@/design-system/fixtures/job-filters";
import { WithFilters } from "@/design-system/fixtures/job-harnesses";
import {
  EMPTY_FILTERS,
  type JobFilters,
} from "@/features/jobs/helpers/filters";

const meta = {
  title: "Shared/Jobs/Status filter",
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "One toggle per status in pipeline order with its count, a status showing alone and a second click showing them all: in the Jobs toolbar on List and Map.",
      },
    },
  },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** The toggles over `initial`, with the counts answered, loading or unavailable. */
function Toggles({
  initial = EMPTY_FILTERS,
  counts = STATUS_COUNTS,
  state = "answered",
}: {
  initial?: JobFilters;
  counts?: Record<string, number>;
  state?: "answered" | "loading" | "unavailable";
}) {
  return (
    <WithFilters initial={initial}>
      {(props) => (
        <StatusFilter
          {...props}
          counts={state === "answered" ? counts : undefined}
          loading={state === "loading"}
          unavailable={state === "unavailable"}
        />
      )}
    </WithFilters>
  );
}

/** Every status with its count; pick one to see it alone. */
export const Typical: Story = {
  name: "Every status with its count",
  render: () => <Toggles />,
};

/** One status picked: it is pressed, the counts stay as they were. */
export const OnePicked: Story = {
  name: "One status picked",
  render: () => (
    <Toggles initial={{ ...EMPTY_FILTERS, statuses: ["shortlisted"] }} />
  ),
};

/** While the list loads, the counts pulse in their place. */
export const Loading: Story = {
  name: "Counts loading",
  render: () => <Toggles state="loading" />,
};

/** When the list could not be loaded, the toggles name no count and still filter. */
export const Unavailable: Story = {
  name: "Counts unavailable",
  render: () => <Toggles state="unavailable" />,
};

/** No posting yet: every count is zero. */
export const NoPosting: Story = {
  name: "No posting yet",
  render: () => <Toggles counts={{}} />,
};

/** On a phone, one row that scrolls sideways, as the toolbar holds it. */
export const OnAPhone: Story = {
  name: "On a phone",
  render: () => (
    <div className="max-w-sm overflow-x-auto [scrollbar-width:none]">
      <Toggles />
    </div>
  ),
};
