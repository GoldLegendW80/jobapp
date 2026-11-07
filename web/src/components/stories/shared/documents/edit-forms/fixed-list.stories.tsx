import type { Meta, StoryObj } from "@storybook/react-vite";

import { FixedList } from "@/components/shared/documents/edit-forms/fixed-list";
import { DocumentForm } from "@/design-system/fixtures/document-form";
import { FIXED_LISTS } from "@/features/documents/helpers/fixed-facts";

const listOf = (name: string) => {
  const list = FIXED_LISTS.find((one) => one.name === name);
  if (list === undefined) throw new Error(`No fixed list ${name}`);
  return list;
};

const meta = {
  title: "Shared/Documents/Fixed facts list",
  component: FixedList,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "A list of a base CV's own facts (employers, schools, languages), used where the profile is still empty, under Not printed in the document view.",
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
} satisfies Meta<typeof FixedList>;

export default meta;
// Each story renders its fields around a form of its own: no args.
type Story = StoryObj;

/** One employer of the base CV. */
export const Employers: Story = {
  name: "Employers",
  render: () => (
    <DocumentForm
      initial={{
        fixed: {
          employers: [
            {
              key: "harbor-works",
              organisation: "Harbor Works",
              title: "Intern",
              start: "2016-06",
              end: "2016-09",
              location: "Marseille",
            },
          ],
        },
      }}
    >
      {(form) => <FixedList form={form} list={listOf("employers")} />}
    </DocumentForm>
  ),
};

/** No school yet: only Add. */
export const Empty: Story = {
  name: "No school yet",
  render: () => (
    <DocumentForm initial={{ fixed: { schools: [] } }}>
      {(form) => <FixedList form={form} list={listOf("schools")} />}
    </DocumentForm>
  ),
};
