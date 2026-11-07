import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";

import { StatusMenu } from "@/components/shared/jobs/filters/status-menu";
import { WithFilters } from "@/design-system/fixtures/job-harnesses";
import { EMPTY_FILTERS } from "@/features/jobs/helpers/filters";

const meta = {
  title: "Shared/Jobs/Status menu",
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The statuses to show, several at once, kept in pipeline order: the first field of the Filters panel in the Jobs toolbar.",
      },
      story: { inline: false, height: "440px" },
    },
  },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** No status picked: all of them. */
export const Every: Story = {
  name: "Every status",
  render: () => (
    <WithFilters>{(props) => <StatusMenu {...props} />}</WithFilters>
  ),
};

/** Two statuses picked. */
export const TwoPicked: Story = {
  name: "Two statuses picked",
  render: () => (
    <WithFilters
      initial={{ ...EMPTY_FILTERS, statuses: ["shortlisted", "cv_ready"] }}
    >
      {(props) => <StatusMenu {...props} />}
    </WithFilters>
  ),
};

/** Open, with the pipeline to tick. */
export const Open: Story = {
  name: "Open the menu",
  render: TwoPicked.render,
  play: async ({ canvasElement }) => {
    await userEvent.click(
      within(canvasElement).getByRole("button", { name: /^Status/ }),
    );
  },
};
