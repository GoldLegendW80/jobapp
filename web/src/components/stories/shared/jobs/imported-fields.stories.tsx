import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";

import { ImportedFields } from "@/components/shared/jobs/posting/imported-fields";
import { WORKED_ON_POSTING } from "@/design-system/fixtures/posting-parts";

const meta = {
  title: "Shared/Jobs/Imported fields",
  component: ImportedFields,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "What an import brought that the app has no place for, folded until asked for: the last section of a posting's main column, only when there is such a field.",
      },
    },
  },
  args: { fields: Object.entries(WORKED_ON_POSTING.legacy) },
} satisfies Meta<typeof ImportedFields>;

export default meta;
type Story = StoryObj<typeof meta>;

const show = async ({ canvasElement }: { canvasElement: HTMLElement }) => {
  await userEvent.click(
    within(canvasElement).getByRole("button", { name: /^Show/ }),
  );
};

/** Folded: the button says how many fields it holds. */
export const Folded: Story = { name: "Folded" };

/** Open: text as it came, anything else as indented JSON. */
export const Open: Story = { name: "Show the fields", play: show };

/** One field. */
export const One: Story = {
  name: "One field",
  args: { fields: [["sheet_row", "jobs.csv, row 12"]] },
  play: show,
};
