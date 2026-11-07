import type { Meta, StoryObj } from "@storybook/react-vite";

import { ChosenAnswers } from "@/components/shared/apply/agent-fields";
import { CHOSEN } from "@/design-system/fixtures/shared-apply";

const meta = {
  title: "Shared/Apply/Chosen answers",
  component: ChosenAnswers,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The answers One click Apply's assistant chose for the person, each with its reason: in the run report of Your application, once the application was sent.",
      },
    },
  },
  args: { chosen: CHOSEN },
} satisfies Meta<typeof ChosenAnswers>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Three answers the assistant chose, each with why. */
export const ThreeAnswers: Story = {
  name: "Answers the assistant chose",
};

/** The assistant chose nothing: the table does not show. */
export const NoneChosen: Story = {
  name: "Nothing chosen, nothing shows",
  args: { chosen: [] },
};

/** On a phone, the reasons wrap under each answer. */
export const OnAPhone: Story = {
  name: "On a phone",
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
};
