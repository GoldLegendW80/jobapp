import type { Meta, StoryObj } from "@storybook/react-vite";
import { screen, userEvent, within } from "storybook/test";

import { AddPostingButton } from "@/components/shared/jobs/jobs-actions/add-posting-button";
import { fakeAddPosting } from "@/design-system/fixtures/jobs-actions";
import { withApp } from "@/design-system/with-app";

const meta = {
  title: "Shared/Jobs/Add posting button",
  component: AddPostingButton,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "Add a posting, an outline button beside Find jobs, which opens the dialog to add one by hand: in the Jobs header, and in the empty list.",
      },
      story: { inline: false, height: "760px" },
    },
  },
  beforeEach: () => {
    fakeAddPosting();
  },
} satisfies Meta<typeof AddPostingButton>;

export default meta;
type Story = StoryObj<typeof meta>;

/** In the Jobs header. */
export const Typical: Story = { name: "In the header" };

/** In the empty list, where it says "by hand" beside Find jobs. */
export const ByHand: Story = {
  name: "In the empty list",
  args: { byHand: true },
};

/** Adding a posting closes the dialog with a toast naming its fit, then opens its page. */
export const AddAPosting: Story = {
  name: "Add a posting",
  play: async ({ canvasElement }) => {
    await userEvent.click(
      within(canvasElement).getByRole("button", { name: "Add a posting" }),
    );
    await userEvent.type(
      await screen.findByLabelText("Company"),
      "Atelier Brume",
    );
    await userEvent.type(screen.getByLabelText("Title"), "Data engineer");
    await userEvent.click(screen.getByRole("button", { name: "Add posting" }));
  },
};
