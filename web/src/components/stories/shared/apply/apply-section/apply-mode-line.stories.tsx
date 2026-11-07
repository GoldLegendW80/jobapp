import type { Meta, StoryObj } from "@storybook/react-vite";
import { MemoryRouter } from "react-router";
import { fn } from "storybook/test";

import { ApplyModeLine } from "@/components/shared/apply/apply-section/apply-mode-line";
import type { ApplySettings } from "@/features/apply/api/settings-api";
import { defaultApplySettings } from "@/test/handlers";

const SENDS: ApplySettings = { ...defaultApplySettings };

const meta = {
  title: "Shared/Apply/Apply section/Apply mode line",
  component: ApplyModeLine,
  tags: ["autodocs"],
  decorators: [
    (Story) => (
      <MemoryRouter>
        <div className="max-w-xl">
          <Story />
        </div>
      </MemoryRouter>
    ),
  ],
  parameters: {
    docs: {
      description: {
        component:
          "Under Apply and Apply myself in Your application: what Apply does under the person's review setting, with the link to change it, and what Apply myself does.",
      },
    },
  },
  args: { settings: SENDS, failed: false, onRetry: fn() },
} satisfies Meta<typeof ApplyModeLine>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The review box unticked, the default: the assistant fills the form and sends it. */
export const ApplySends: Story = {
  name: "Apply sends the form",
};

/** The review box ticked: the person checks each form and sends it. */
export const ApplyWaitsForReview: Story = {
  name: "Apply waits for your review",
  args: { settings: { ...SENDS, review_before_send: true } },
};

/** The settings are being read: Apply waits. */
export const ReadingTheSettings: Story = {
  name: "Reading your settings",
  args: { settings: undefined },
};

/** The settings could not be read: Apply waits, and Try again reads them again. */
export const SettingsNotRead: Story = {
  name: "Your settings could not be read",
  args: { settings: undefined, failed: true },
};
