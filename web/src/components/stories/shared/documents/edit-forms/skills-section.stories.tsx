import type { Meta, StoryObj } from "@storybook/react-vite";

import { SkillsSection } from "@/components/shared/documents/edit-forms/skills-section";
import { DocumentForm } from "@/design-system/fixtures/document-form";
import { CV_DRAFT } from "@/design-system/fixtures/documents";

const meta = {
  title: "Shared/Documents/Skills fields",
  component: SkillsSection,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The skills of a CV opened in place on its page: groups of terms, each with its label, reordered, removed or added.",
      },
    },
  },
  decorators: [
    (Story) => (
      <div className="max-w-xl">
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof SkillsSection>;

export default meta;
// Each story renders its fields around a form of its own: no args.
type Story = StoryObj;

/** Two groups of terms. */
export const Typical: Story = {
  name: "Two groups",
  render: () => (
    <DocumentForm initial={CV_DRAFT}>
      {(form) => <SkillsSection form={form} />}
    </DocumentForm>
  ),
};

/** No group yet. */
export const Empty: Story = {
  name: "No group yet",
  render: () => (
    <DocumentForm initial={{ skills: [] }}>
      {(form) => <SkillsSection form={form} />}
    </DocumentForm>
  ),
};
