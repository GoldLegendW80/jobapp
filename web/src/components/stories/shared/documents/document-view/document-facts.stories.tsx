import type { Meta, StoryObj } from "@storybook/react-vite";

import { DocumentFacts } from "@/components/shared/documents/document-view/document-facts";
import { postingDocument } from "@/design-system/fixtures/documents";
import { POSTING_DETAIL } from "@/design-system/fixtures/postings";
import { fakeVersion } from "@/test/documents";

const latest = fakeVersion({ number: 2 });

const meta = {
  title: "Shared/Documents/Document facts",
  component: DocumentFacts,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Details of a document in the side rail of the document view: its template, its language, and whether the version shown fits one page.",
      },
    },
  },
  args: {
    document: postingDocument("cv", POSTING_DETAIL.id, { latest }),
    shown: latest,
  },
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof DocumentFacts>;

export default meta;
type Story = StoryObj<typeof meta>;

/** One page, in English, with Classic. */
export const OnePage: Story = { name: "Fits one page" };

/** Longer than one page, in red. */
export const Overflow: Story = {
  name: "Longer than one page",
  args: { shown: { ...latest, fit: "overflow", pages: 2 } },
};

/** No version yet: no length to say. */
export const NoVersion: Story = {
  name: "No version yet",
  args: { shown: undefined },
};
