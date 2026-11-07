import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";

import { TypeMenu } from "@/components/shared/jobs/filters/type-menu";
import { WithFilters } from "@/design-system/fixtures/job-harnesses";
import { EMPTY_FILTERS } from "@/features/jobs/helpers/filters";

const meta = {
  title: "Shared/Jobs/Type menu",
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The contract types to show, several at once: in the Filters panel of the Jobs toolbar, under the statuses.",
      },
      story: { inline: false, height: "360px" },
    },
  },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** No type picked: all of them. */
export const Every: Story = {
  name: "Every type",
  render: () => <WithFilters>{(props) => <TypeMenu {...props} />}</WithFilters>,
};

/** Permanent contracts only. */
export const OnePicked: Story = {
  name: "One type picked",
  render: () => (
    <WithFilters initial={{ ...EMPTY_FILTERS, types: ["CDI"] }}>
      {(props) => <TypeMenu {...props} />}
    </WithFilters>
  ),
};

/** Open, with the contract types to tick. */
export const Open: Story = {
  name: "Open the menu",
  render: OnePicked.render,
  play: async ({ canvasElement }) => {
    await userEvent.click(
      within(canvasElement).getByRole("button", { name: /^Type/ }),
    );
  },
};
