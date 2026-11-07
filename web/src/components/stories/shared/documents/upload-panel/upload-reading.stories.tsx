import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { UploadReading } from "@/components/shared/documents/upload-panel/upload-reading";
import { fakeDocument } from "@/test/ingestion";

const FILE = fakeDocument({
  id: "doc-cv",
  name: "camille-martin-cv.pdf",
  kind: "pdf",
  mime: "application/pdf",
  size: 184_320,
  pages: 2,
  created_at: "2026-11-04T08:12:00Z",
});

const meta = {
  title: "Shared/Documents/Upload reading",
  component: UploadReading,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The upload panel while the file sent is read: reading, failed (and why), or read and waiting for the consent to convert it.",
      },
    },
  },
  args: {
    file: FILE,
    busy: false,
    declined: false,
    onConvert: fn(),
    onKeep: fn(),
  },
} satisfies Meta<typeof UploadReading>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Read: the conversion waits for the consent. */
export const Read: Story = { name: "Read, waiting for the consent" };

/** The consent was declined: a button asks again. */
export const Declined: Story = {
  name: "The consent declined",
  args: { declined: true },
};

/** Still being read. */
export const Reading: Story = {
  name: "Being read",
  args: { file: { ...FILE, status: "pending" } },
};

/** It could not be read. */
export const Failed: Story = {
  name: "Could not be read",
  args: {
    file: {
      ...FILE,
      status: "failed",
      failed_reason: "The file is protected by a password.",
    },
  },
};
