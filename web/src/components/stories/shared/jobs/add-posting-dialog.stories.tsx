import type { Meta, StoryObj } from "@storybook/react-vite";
import { screen, userEvent } from "storybook/test";

import { AddPostingDialog } from "@/components/shared/jobs/jobs-actions/add-posting-dialog";
import { fakeAddPosting } from "@/design-system/fixtures/jobs-actions";
import { phoneScreen } from "@/design-system/fixtures/phone-screen";
import { withApp } from "@/design-system/with-app";

const meta = {
  title: "Shared/Jobs/Add posting dialog",
  component: AddPostingDialog,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "A posting from a site the app does not search, typed or pasted by hand and scored at once: opened by Add a posting on Jobs, and by Add a mission by hand on the Search tab.",
      },
      story: { inline: false, height: "760px" },
    },
  },
  args: { onClose: () => undefined },
  beforeEach: () => {
    fakeAddPosting();
  },
} satisfies Meta<typeof AddPostingDialog>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Fill in a company, a title, and the text to score it. */
async function fill() {
  await userEvent.type(
    await screen.findByLabelText("Company"),
    "Atelier Brume",
  );
  await userEvent.type(screen.getByLabelText("Title"), "Data engineer");
  await userEvent.click(screen.getByRole("button", { name: "Add posting" }));
}

/** An empty form: Add posting waits for a company and a title. */
export const Empty: Story = { name: "An empty form" };

/** A freelance mission: the contract comes preselected. */
export const Mission: Story = {
  name: "A mission by hand",
  args: { initial: { contract_type: "Freelance" } },
};

/** While it adds and scores, the buttons wait. */
export const Adding: Story = {
  name: "Adding",
  beforeEach: () => {
    fakeAddPosting("pending");
  },
  play: fill,
};

/** A posting already in the list links to it instead of making a twin. */
export const Duplicate: Story = {
  name: "Already in your list",
  beforeEach: () => {
    fakeAddPosting("duplicate");
  },
  play: fill,
};

/** When the server fails, nothing is added and the form keeps what was typed. */
export const Fails: Story = {
  name: "Not added",
  beforeEach: () => {
    fakeAddPosting("error");
  },
  play: fill,
};

/** On a phone the fields stack and the dialog scrolls. */
export const OnAPhone: Story = {
  name: "On a phone",
  beforeEach: phoneScreen,
};
