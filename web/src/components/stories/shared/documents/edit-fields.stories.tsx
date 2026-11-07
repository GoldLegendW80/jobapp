import type { Meta, StoryObj } from "@storybook/react-vite";

import {
  ListField,
  TextField,
} from "@/components/shared/documents/edit-fields";
import { DocumentForm } from "@/design-system/fixtures/document-form";
import { CV_DRAFT } from "@/design-system/fixtures/documents";
import { MAX } from "@/features/documents/helpers/edit-content";

const meta = {
  title: "Shared/Documents/Edit fields",
  component: TextField,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The fields of a document's text, opened in place on its page: a text with its count against the template's length, its notes and its refusal; a list of texts (highlights, bullets, paragraphs) reordered with Alt and an arrow key, each removed or added.",
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
} satisfies Meta<typeof TextField>;

export default meta;
// Each story renders its fields around a form of its own: no args.
type Story = StoryObj;

/** A text counted against what the template suggests. */
export const Text: Story = {
  name: "A text",
  render: () => (
    <DocumentForm initial={CV_DRAFT}>
      {(form) => (
        <TextField
          form={form}
          path={["summary"]}
          label="Summary"
          max={MAX.summary}
          multiline
        />
      )}
    </DocumentForm>
  ),
};

/** A note of the fact check, and a refusal of the last save. */
export const NoteAndRefusal: Story = {
  name: "With a note and a refusal",
  render: () => (
    <DocumentForm
      initial={CV_DRAFT}
      notes={
        new Map([
          [
            "headline",
            [
              {
                sentence: "A number your profile does not state",
                excerpt: "12",
              },
            ],
          ],
        ])
      }
      errors={new Map([["headline", "This is longer than allowed."]])}
    >
      {(form) => (
        <TextField
          form={form}
          path={["headline"]}
          label="Headline"
          max={MAX.headline}
        />
      )}
    </DocumentForm>
  ),
};

/** A list of texts: move one with Alt and an arrow, remove it, add another. */
export const List: Story = {
  name: "A list of texts",
  render: () => (
    <DocumentForm initial={CV_DRAFT}>
      {(form) => (
        <ListField
          form={form}
          path={["highlights"]}
          legend="Highlights"
          noun="Highlight"
          max={MAX.highlights}
          itemMax={MAX.bullet}
        />
      )}
    </DocumentForm>
  ),
};

/** An empty list: only Add. */
export const EmptyList: Story = {
  name: "An empty list",
  render: () => (
    <DocumentForm initial={{}}>
      {(form) => (
        <ListField
          form={form}
          path={["competencies"]}
          legend="Competencies"
          noun="Competency"
          max={MAX.competencies}
          itemMax={MAX.term}
        />
      )}
    </DocumentForm>
  ),
};
