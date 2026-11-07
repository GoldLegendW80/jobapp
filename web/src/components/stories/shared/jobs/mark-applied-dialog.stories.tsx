import type { Meta, StoryObj } from "@storybook/react-vite";

import { MarkAppliedDialog } from "@/components/shared/jobs/jobs-actions/mark-applied-dialog";

const meta = {
  title: "Shared/Jobs/Mark applied dialog",
  component: MarkAppliedDialog,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The question before many postings are marked as applied, with how to undo it: opened by Set status in the bulk bar of Jobs.",
      },
      story: { inline: false, height: "320px" },
    },
  },
  args: {
    open: true,
    count: 12,
    onOpenChange: () => undefined,
    onConfirm: () => undefined,
  },
} satisfies Meta<typeof MarkAppliedDialog>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Twelve postings ticked. */
export const Several: Story = { name: "Several postings" };

/** One posting ticked. */
export const One: Story = { name: "One posting", args: { count: 1 } };
