import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";
import { userEvent, within } from "storybook/test";

import { MultiSelectMenu } from "@/components/shared/jobs/filters/multi-select-menu";

const OPTIONS = [
  { value: "remote", label: "Remote" },
  { value: "hybrid", label: "Hybrid" },
  { value: "onsite", label: "On site" },
];

/** The menu over the three work modes, holding what is ticked. */
function Menu({ initial = [] }: { initial?: string[] }) {
  const [selected, setSelected] = useState(initial);
  return (
    <MultiSelectMenu
      label="Work mode"
      options={OPTIONS}
      selected={selected}
      onChange={setSelected}
    />
  );
}

const meta = {
  title: "Shared/Jobs/Multi select menu",
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "A menu of boxes to tick that stays open to pick several, its button saying what is picked: the Status and Type menus of the Filters panel are made of it.",
      },
      story: { inline: false, height: "280px" },
    },
  },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** Nothing ticked reads "All". */
export const NothingPicked: Story = {
  name: "Nothing picked",
  render: () => <Menu />,
};

/** One ticked reads its name. */
export const OnePicked: Story = {
  name: "One picked",
  render: () => <Menu initial={["remote"]} />,
};

/** Several ticked read their count. */
export const SeveralPicked: Story = {
  name: "Several picked",
  render: () => <Menu initial={["remote", "hybrid"]} />,
};

/** Open, it stays open while boxes are ticked. */
export const Open: Story = {
  name: "Open the menu",
  render: () => <Menu initial={["hybrid"]} />,
  play: async ({ canvasElement }) => {
    await userEvent.click(
      within(canvasElement).getByRole("button", { name: /^Work mode/ }),
    );
  },
};
