import type { Meta, StoryObj } from "@storybook/react-vite";

import { PrintedPage } from "@/components/shared/documents/document-view/printed-page";
import { fakePageImages } from "@/design-system/fixtures/documents";
import { fakeVersion } from "@/test/documents";

const version = fakeVersion({ number: 2 });

const meta = {
  title: "Shared/Documents/Printed page",
  component: PrintedPage,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Page 1 of the PDF of the version shown, as the renderer drew it: the page that is sent, in the side rail of the document view. Its PDF links are its actions when no page header holds them.",
      },
    },
  },
  args: { documentId: "cv-doc", version, pdf: false },
  beforeEach: () => {
    fakePageImages();
  },
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof PrintedPage>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The page as it is sent. */
export const Typical: Story = { name: "The page as it is sent" };

/** With its PDF links, when no header holds them. */
export const WithPdfLinks: Story = {
  name: "With its PDF links",
  args: { pdf: true },
};

/** Longer than one page: a red frame. */
export const Overflow: Story = {
  name: "Longer than one page",
  args: { version: { ...version, fit: "overflow", pages: 2 } },
};

/** A PDF the command line made: no preview, open it. */
export const NoPreview: Story = {
  name: "A PDF with no preview",
  args: { version: { ...version, has_preview: false } },
};

/** A version whose page is not drawn yet. */
export const NoPage: Story = {
  name: "No page yet",
  args: { version: { ...version, has_pdf: false } },
};
