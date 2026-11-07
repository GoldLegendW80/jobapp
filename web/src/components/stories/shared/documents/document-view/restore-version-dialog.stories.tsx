import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { RestoreVersionDialog } from "@/components/shared/documents/document-view/restore-version-dialog";
import { fakeVersion } from "@/test/documents";

const meta = {
  title: "Shared/Documents/Restore version dialog",
  component: RestoreVersionDialog,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Asks before a version's text replaces the draft, from Restore in the versions of the document view. The version itself stays as it is.",
      },
    },
  },
  args: {
    version: fakeVersion({ number: 1 }),
    busy: false,
    onCancel: fn(),
    onRestore: fn(),
  },
} satisfies Meta<typeof RestoreVersionDialog>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Restore version 1 into the draft. */
export const Typical: Story = { name: "Restore a version" };

/** A sent version: it stays as it was sent. */
export const SentVersion: Story = {
  name: "Restore a sent version",
  args: {
    version: fakeVersion({
      number: 1,
      frozen_at: "2026-11-02T10:00:00Z",
      frozen_reason: "marked_sent",
    }),
  },
};

/** While the restore runs, both buttons wait. */
export const Restoring: Story = { name: "Restoring", args: { busy: true } };
