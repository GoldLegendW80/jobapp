import type { Meta, StoryObj } from "@storybook/react-vite";

import { FitNote } from "@/components/shared/documents/document-view/fit-note";
import { postingDocument } from "@/design-system/fixtures/documents";
import { POSTING_DETAIL } from "@/design-system/fixtures/postings";
import { withApp } from "@/design-system/with-app";
import { fakeVersion } from "@/test/documents";

const over = fakeVersion({ number: 2, fit: "overflow", pages: 2 });

const meta = {
  title: "Shared/Documents/Fit note",
  component: FitNote,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "Above a page that runs over one page, in the document view: it is kept and can be downloaded as it is, and how to make it fit; for the latest version of a posting's CV, Make it fit asks the writer for a shorter one.",
      },
    },
  },
  args: {
    document: postingDocument("cv", POSTING_DETAIL.id, { latest: over }),
    generation: null,
    version: over,
    isLatest: true,
  },
} satisfies Meta<typeof FitNote>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A posting's CV: Make it fit is offered. */
export const MakeItFit: Story = { name: "Make it fit offered" };

/** A letter has no shortening pass: shorten by hand or choose a compact template. */
export const Letter: Story = {
  name: "A letter",
  args: {
    document: postingDocument("letter", POSTING_DETAIL.id, { latest: over }),
  },
};

/** An earlier version that ran over: said, with nothing to run. */
export const EarlierVersion: Story = {
  name: "An earlier version",
  args: { isLatest: false },
};
