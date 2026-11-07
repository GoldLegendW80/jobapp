import type { Meta, StoryObj } from "@storybook/react-vite";

import { FormSection } from "@/components/shared/documents/edit-forms/form-section";

const meta = {
  title: "Shared/Documents/Form section",
  component: FormSection,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "A titled card of the fields not printed on the page (For you, a base CV's facts), under Not printed in the document view.",
      },
    },
  },
  args: {
    title: "For you",
    children: (
      <p className="text-sm text-muted-foreground">
        What this CV is for and your notes.
      </p>
    ),
  },
} satisfies Meta<typeof FormSection>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A title and its fields. */
export const Typical: Story = { name: "A titled group" };
