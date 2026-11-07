import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { ConsentDialog } from "@/components/shared/documents/consent-dialog";
import { fakeConsent } from "@/test/documents";

const meta = {
  title: "Shared/Documents/Consent dialog",
  component: ConsentDialog,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "What is sent, to whom and what it costs, before a model writes a CV, changes a document in the chat or converts an uploaded CV: the box is ticked for the version shown. Opened from the CV tab, the upload panel and the document chat.",
      },
    },
  },
  args: {
    consent: fakeConsent(),
    open: true,
    busy: false,
    onAgree: fn(),
    onClose: fn(),
  },
} satisfies Meta<typeof ConsentDialog>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The text, the estimates, the box to tick. */
export const Typical: Story = { name: "Read and agree" };

/** With the consent of the Fill my profile run asked in the same dialog. */
export const WithRun: Story = {
  name: "With the Fill my profile run",
  args: {
    run: {
      version: 2,
      text: "The app sends your uploaded CV to the model to complete your profile.",
    },
  },
};

/** While the agreement is recorded. */
export const Recording: Story = { name: "Recording", args: { busy: true } };

/** The agreement could not be recorded. */
export const Failed: Story = {
  name: "Could not be recorded",
  args: { error: "The service is busy. Try again in a moment." },
};
