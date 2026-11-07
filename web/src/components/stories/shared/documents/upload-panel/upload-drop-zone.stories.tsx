import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { UploadDropZone } from "@/components/shared/documents/upload-panel/upload-drop-zone";

const meta = {
  title: "Shared/Documents/Upload drop zone",
  component: UploadDropZone,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Where a CV file is dropped or chosen, at the top of the upload panel.",
      },
    },
  },
  args: { busy: false, onFile: fn() },
} satisfies Meta<typeof UploadDropZone>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Ready for a file. */
export const Ready: Story = {};

/** While a file is sent: it takes no other. */
export const Busy: Story = {
  name: "While a file is sent",
  args: { busy: true },
};
