import type { Meta, StoryObj } from "@storybook/react-vite";

import { UploadStatus } from "@/components/shared/documents/upload-panel/upload-status";

const meta = {
  title: "Shared/Documents/Upload status",
  component: UploadStatus,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "A line of the upload panel saying what runs now, with a spinner: sending, reading, converting.",
      },
    },
  },
  args: { children: "Reading your CV" },
} satisfies Meta<typeof UploadStatus>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The file is read. */
export const Reading: Story = { name: "Reading" };

/** The CV is converted. */
export const Converting: Story = {
  name: "Converting",
  args: { children: "Turning your CV into one you can edit" },
};
