import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";

import { FitRange } from "@/components/shared/jobs/filters/fit-range";
import { WithFilters } from "@/design-system/fixtures/job-harnesses";
import { EMPTY_FILTERS } from "@/features/jobs/helpers/filters";

const meta = {
  title: "Shared/Jobs/Fit range",
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The lowest and highest fit to show, applied on Enter or when focus leaves and only as a valid range: in the Filters panel, above the fit histogram.",
      },
    },
  },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** No range: every fit. */
export const NoRange: Story = {
  name: "No range",
  render: () => <WithFilters>{(props) => <FitRange {...props} />}</WithFilters>,
};

/** From 70 to 100, the good fits. */
export const RangeSet: Story = {
  name: "A range set",
  render: () => (
    <WithFilters initial={{ ...EMPTY_FILTERS, fitMin: 70, fitMax: 100 }}>
      {(props) => <FitRange {...props} />}
    </WithFilters>
  ),
};

/** A lowest fit above the highest is not applied, and says why. */
export const WrongWayRound: Story = {
  name: "The lowest above the highest",
  render: NoRange.render,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.type(canvas.getByLabelText("Fit from"), "80");
    await userEvent.type(canvas.getByLabelText("Fit to"), "20{Enter}");
  },
};

/** A fit outside 0 to 100 is not a fit. */
export const NotAFit: Story = {
  name: "A number that is not a fit",
  render: NoRange.render,
  play: async ({ canvasElement }) => {
    await userEvent.type(
      within(canvasElement).getByLabelText("Fit from"),
      "140{Enter}",
    );
  },
};
