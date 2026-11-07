import type { Meta, StoryObj } from "@storybook/react-vite";
import { screen, userEvent } from "storybook/test";

import { RescoreDialog } from "@/components/shared/jobs/jobs-actions/rescore-dialog";
import { fakeRescore } from "@/design-system/fixtures/jobs-actions";
import { POSTINGS } from "@/design-system/fixtures/postings";
import { withApp } from "@/design-system/with-app";

const meta = {
  title: "Shared/Jobs/Rescore dialog",
  component: RescoreDialog,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "Scores postings again against the profile's skills, keeping the fits set by hand unless the person ticks otherwise, with its progress: opened from More actions on Jobs.",
      },
      story: { inline: false, height: "420px" },
    },
  },
  args: { onClose: () => undefined },
  beforeEach: () => {
    fakeRescore();
  },
} satisfies Meta<typeof RescoreDialog>;

export default meta;
type Story = StoryObj<typeof meta>;

const rescore = async () => {
  await userEvent.click(await screen.findByRole("button", { name: "Rescore" }));
};

/** Every posting. */
export const All: Story = { name: "Rescore all postings" };

/** The postings ticked in the list. */
export const Selected: Story = {
  name: "Rescore the selected postings",
  args: { ids: POSTINGS.slice(0, 4).map((job) => job.id) },
};

/** Replacing the fits set by hand is said plainly, and the button turns red. */
export const ReplaceManual: Story = {
  name: "Replace the fits set by hand",
  play: async () => {
    await userEvent.click(
      await screen.findByLabelText("Also replace the fits I set by hand"),
    );
  },
};

/** A long rescore counts what is done and what is left. */
export const Running: Story = {
  name: "Rescoring",
  beforeEach: () => {
    fakeRescore("running");
  },
  play: rescore,
};

/** Without skills in the profile there is nothing to score with: the dialog links there. */
export const Refused: Story = {
  name: "No skills to score with",
  beforeEach: () => {
    fakeRescore("refused");
  },
  play: rescore,
};

/** A rescore that stops says so; what was rescored stays. */
export const Fails: Story = {
  name: "The rescore stops",
  beforeEach: () => {
    fakeRescore("error");
  },
  play: rescore,
};
