import type { Meta, StoryObj } from "@storybook/react-vite";

import { PostingDocumentsSection } from "@/components/shared/documents/posting-documents";
import {
  fakePostingDocuments,
  postingDocument,
} from "@/design-system/fixtures/documents";
import { POSTING_DETAIL } from "@/design-system/fixtures/postings";
import { withApp } from "@/design-system/with-app";
import { fakeVersion } from "@/test/documents";

const JOB = POSTING_DETAIL;
const latest = fakeVersion({ number: 2 });

const meta = {
  title: "Shared/Documents/Posting documents",
  component: PostingDocumentsSection,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    at: `/jobs/${JOB.id}`,
    docs: {
      description: {
        component:
          "The Documents section of a posting's page: its CV and letter as two rows, each with its page 1, its state and what to do next (write, open, download, make it fit), and Write both now while neither is written.",
      },
    },
  },
  args: { job: JOB },
} satisfies Meta<typeof PostingDocumentsSection>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Neither written yet: Write both now. */
export const NothingWritten: Story = {
  name: "Nothing written yet",
  beforeEach: () => {
    fakePostingDocuments(JOB, []);
  },
};

/** Both written, each on one page. */
export const BothReady: Story = {
  name: "Both written",
  beforeEach: () => {
    fakePostingDocuments(JOB, [
      postingDocument("cv", JOB.id, {
        latest,
        generation: { status: "done", wait_reason: null },
      }),
      postingDocument("letter", JOB.id, {
        latest,
        generation: { status: "done", wait_reason: null },
      }),
    ]);
  },
};

/** The CV being written; the letter follows it. */
export const Writing: Story = {
  name: "The CV being written",
  beforeEach: () => {
    fakePostingDocuments(JOB, [
      postingDocument("cv", JOB.id, {
        generation: { status: "running", wait_reason: null },
      }),
    ]);
  },
};

/** The CV runs over one page: Make it fit. */
export const Overflow: Story = {
  name: "The CV runs over one page",
  beforeEach: () => {
    fakePostingDocuments(JOB, [
      postingDocument("cv", JOB.id, {
        latest: { ...latest, fit: "overflow", pages: 2 },
        generation: { status: "done", wait_reason: null },
      }),
    ]);
  },
};

/** At a phone's width the rows stack. */
export const OnAPhone: Story = {
  name: "On a phone",
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
  beforeEach: () => {
    fakePostingDocuments(JOB, [
      postingDocument("cv", JOB.id, {
        latest,
        generation: { status: "done", wait_reason: null },
      }),
    ]);
  },
};
