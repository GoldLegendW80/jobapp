import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";

import { AdzunaKeyCard } from "@/components/shared/jobs/job-feeds-ui";
import { fakeAdzunaKey } from "@/design-system/fixtures/jobs-actions";
import { withApp } from "@/design-system/with-app";

const meta = {
  title: "Shared/Jobs/Adzuna key card",
  component: AdzunaKeyCard,
  tags: ["autodocs"],
  decorators: [
    withApp,
    (Story) => (
      <div className="max-w-xl">
        <Story />
      </div>
    ),
  ],
  parameters: {
    docs: {
      description: {
        component:
          "The person's own free Adzuna key, stored encrypted and never shown back, which lets Find jobs read Adzuna: a card of the Account page.",
      },
      story: { inline: false, height: "520px" },
    },
  },
  beforeEach: () => {
    fakeAdzunaKey();
  },
} satisfies Meta<typeof AdzunaKeyCard>;

export default meta;
type Story = StoryObj<typeof meta>;

/** No key stored: Adzuna is skipped until one is saved. */
export const NoKey: Story = { name: "No key stored" };

/** A key stored: its last four characters, when it was saved, and Remove key. */
export const KeyStored: Story = {
  name: "A key stored",
  beforeEach: () => {
    fakeAdzunaKey({ stored: true });
  },
};

/** Pasting both parts and Save key stores it; the line above says so. */
export const SaveAKey: Story = {
  name: "Save a key",
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.type(
      await canvas.findByLabelText("Application ID"),
      "a1b2c3d4",
    );
    await userEvent.type(
      canvas.getByLabelText("Application key"),
      "0000example0000key07f3a",
    );
    await userEvent.click(canvas.getByRole("button", { name: "Save key" }));
  },
};

/** One part alone is not saved, and the card says what is missing. */
export const OnePartOnly: Story = {
  name: "Only one part pasted",
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.type(
      await canvas.findByLabelText("Application ID"),
      "a1b2c3d4",
    );
    await userEvent.click(canvas.getByRole("button", { name: "Save key" }));
  },
};

/** While the server answers, the line says it is checking. */
export const Checking: Story = {
  name: "Checking for a key",
  beforeEach: () => {
    fakeAdzunaKey({ feeds: "pending" });
  },
};

/** When the check fails, the card says so and can still save a key. */
export const CheckFails: Story = {
  name: "The check fails",
  beforeEach: () => {
    fakeAdzunaKey({ feeds: "error" });
  },
};

/** A server without its credentials key cannot store any key, and says so. */
export const ServerCannotStore: Story = {
  name: "The server cannot store keys",
  beforeEach: () => {
    fakeAdzunaKey({ feeds: "no_keys" });
  },
};
