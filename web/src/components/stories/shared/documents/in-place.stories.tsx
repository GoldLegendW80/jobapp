import type { Meta, StoryObj } from "@storybook/react-vite";

import { TextField } from "@/components/shared/documents/edit-fields";
import { InPlace } from "@/components/shared/documents/in-place";
import { PageForm } from "@/components/shared/documents/page-form";
import { DocumentForm } from "@/design-system/fixtures/document-form";
import { textAt } from "@/features/documents/helpers/edit-content";

const SUMMARY =
  "Data engineer with seven years on batch and streaming pipelines.";

function Summary({ problems }: { problems?: string[] }) {
  return (
    <DocumentForm initial={{ summary: SUMMARY }}>
      {(form) => (
        <PageForm.Provider value={form}>
          <div className="max-w-xl rounded-md border bg-white p-8 text-[13px] text-[#1d232b]">
            <InPlace
              label="the summary"
              paths={[["summary"]]}
              problems={problems}
              editor={
                <TextField
                  form={form}
                  path={["summary"]}
                  label="Summary"
                  max={600}
                  multiline
                  hideLabel
                />
              }
            >
              <p>{textAt(form.draft, ["summary"])}</p>
            </InPlace>
          </div>
        </PageForm.Provider>
      )}
    </DocumentForm>
  );
}

const meta = {
  title: "Shared/Documents/In place",
  component: InPlace,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "A part of a document's page edited where it is read: the text with a button over it (hover and focus show what is editable); a click or Enter puts its field in its place, Escape puts back the text it had, Done or a click outside keeps it. Used by every text of the document sheet.",
      },
    },
  },
  args: {
    label: "the summary",
    paths: [["summary"]],
    editor: null,
    children: <p>{SUMMARY}</p>,
  },
} satisfies Meta<typeof InPlace>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Read: hover the text or Tab to it, then click or press Enter. */
export const ClickToEdit: Story = {
  name: "Click a text to edit it",
  render: () => <Summary />,
};

/** A note of the fact check stays under the text while it is closed. */
export const WithANote: Story = {
  name: "With a note under the text",
  render: () => (
    <Summary problems={["A number your profile does not state: “seven”"]} />
  ),
};

/** Without a form around it (an earlier version), it is only the text. */
export const ReadOnly: Story = {
  name: "Read only",
  render: (args) => (
    <div className="max-w-xl rounded-md border bg-white p-8 text-[13px]">
      <InPlace {...args} />
    </div>
  ),
};
