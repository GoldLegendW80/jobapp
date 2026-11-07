import type { Meta, StoryObj } from "@storybook/react-vite";

import { VersionMeta } from "@/components/shared/documents/document-view/version-heading";
import { fakeVersion } from "@/test/documents";

const meta = {
  title: "Shared/Documents/Version marks",
  component: VersionMeta,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Under the title of the page in the document view: when the version shown was sent, and that a newer page is being drawn.",
      },
    },
  },
  args: { version: fakeVersion({ number: 2 }), drawing: false },
} satisfies Meta<typeof VersionMeta>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A sent version. */
export const Sent: Story = {
  name: "A sent version",
  args: {
    version: fakeVersion({
      number: 1,
      frozen_at: "2026-11-02T10:00:00Z",
      frozen_reason: "marked_sent",
    }),
  },
};

/** A newer page being drawn. */
export const Drawing: Story = {
  name: "A newer page being drawn",
  args: { drawing: true },
};

/** Nothing to mark: nothing shows. */
export const Nothing: Story = { name: "Nothing to mark" };
