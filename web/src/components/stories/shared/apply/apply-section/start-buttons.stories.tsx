import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { StartButtons } from "@/components/shared/apply/apply-section/start-buttons";
import { withButtonRow } from "./button-row";

const meta = {
  title: "Shared/Apply/Apply section/Start buttons",
  component: StartButtons,
  tags: ["autodocs"],
  decorators: [withButtonRow],
  parameters: {
    docs: {
      description: {
        component:
          "Apply and Apply myself, on a posting with no application yet, in Your application. On LinkedIn before the person allowed it, only how to allow it and the posting's own link.",
      },
    },
  },
  args: {
    busy: false,
    settingsRead: true,
    starting: null,
    linkedinBlocked: false,
    linkedinPosting: null,
    onStart: fn(),
  },
} satisfies Meta<typeof StartButtons>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Apply, the primary action, and Apply myself beside it. */
export const ReadyToApply: Story = {
  name: "Ready to apply",
};

/** Until the settings are read, Apply waits: what it does is the person's setting. */
export const SettingsNotReadYet: Story = {
  name: "Apply waits for your settings",
  args: { settingsRead: false },
};

/** Apply clicked: it spins while the application starts. */
export const StartingApply: Story = {
  name: "Starting Apply",
  args: { busy: true, starting: "apply" },
};

/** Apply myself clicked: it spins while the browser opens. */
export const StartingApplyMyself: Story = {
  name: "Starting Apply myself",
  args: { busy: true, starting: "manual" },
};

/** A LinkedIn posting before LinkedIn is allowed: how to allow it, and the posting's own link. */
export const LinkedInNotAllowed: Story = {
  name: "LinkedIn not allowed yet",
  args: {
    linkedinBlocked: true,
    linkedinPosting: "https://www.linkedin.com/jobs/view/4100000001/",
  },
};

/** The same, for a LinkedIn address that is not a posting: no link. */
export const LinkedInWithoutALink: Story = {
  name: "LinkedIn not allowed, no posting link",
  args: { linkedinBlocked: true, linkedinPosting: null },
};

/** On a phone, the buttons stack and take the whole width. */
export const OnAPhone: Story = {
  name: "On a phone",
  parameters: { phone: true },
};
