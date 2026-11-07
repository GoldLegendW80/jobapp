import type { Meta, StoryObj } from "@storybook/react-vite";

import { TermsField } from "@/components/shared/documents/edit-forms/terms-field";
import { DocumentForm } from "@/design-system/fixtures/document-form";

const meta = {
  title: "Shared/Documents/Terms field",
  component: TermsField,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "A group of skills typed as one line, terms separated by commas, in the skills of a CV.",
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
} satisfies Meta<typeof TermsField>;

export default meta;
// Each story renders its fields around a form of its own: no args.
type Story = StoryObj;

/** Four terms. */
export const Typical: Story = {
  name: "Four terms",
  render: () => (
    <DocumentForm initial={{ items: ["dbt", "Airflow", "Kafka", "Terraform"] }}>
      {(form) => <TermsField form={form} path={["items"]} />}
    </DocumentForm>
  ),
};

/** No term yet. */
export const Empty: Story = {
  name: "No term yet",
  render: () => (
    <DocumentForm initial={{ items: [] }}>
      {(form) => <TermsField form={form} path={["items"]} />}
    </DocumentForm>
  ),
};
