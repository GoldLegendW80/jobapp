import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { PostingDocumentRow } from "@/components/shared/documents/posting-document-row";
import { Card } from "@/components/ui/card";
import {
  fakePostingDocuments,
  postingDocument,
} from "@/design-system/fixtures/documents";
import { POSTING_DETAIL } from "@/design-system/fixtures/postings";
import { withApp } from "@/design-system/with-app";
import { fakeCv, fakeVersion } from "@/test/documents";

const JOB = POSTING_DETAIL;
const latest = fakeVersion({ number: 2 });

const meta = {
  title: "Shared/Documents/Posting document row",
  component: PostingDocumentRow,
  tags: ["autodocs"],
  decorators: [
    withApp,
    (Story) => (
      <Card className="max-w-3xl py-0">
        <Story />
      </Card>
    ),
  ],
  parameters: {
    docs: {
      description: {
        component:
          "One document of a posting in its Documents section: page 1, its state in words, the hiring panel's outcome in one line, and what to do next. The CV row also chooses the base CV it is written from.",
      },
    },
  },
  args: {
    jobId: JOB.id,
    kind: "cv",
    document: postingDocument("cv", JOB.id, {
      latest,
      generation: { status: "done", wait_reason: null },
    }),
    cvs: [fakeCv()],
    choice: undefined,
    onChoose: fn(),
    cvReady: true,
    letterWritten: false,
  },
  beforeEach: () => {
    fakePostingDocuments(JOB, []);
  },
} satisfies Meta<typeof PostingDocumentRow>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Ready, on one page: open it or download its PDF. */
export const Ready: Story = {};

/** Not written yet: Write now. */
export const NotWritten: Story = {
  name: "Not written yet",
  args: { document: undefined },
};

/** The letter follows the CV's hiring panel: no Write meanwhile. */
export const LetterAfterThePanel: Story = {
  name: "The letter after the CV's hiring panel",
  args: { kind: "letter", document: undefined, afterPanel: true },
};

/** Being written. */
export const Writing: Story = {
  name: "Being written",
  args: {
    document: postingDocument("cv", JOB.id, {
      generation: { status: "running", wait_reason: null },
    }),
  },
};

/** Waiting, and where to settle what it waits for. */
export const Waiting: Story = {
  name: "Waiting for the consent",
  args: {
    document: postingDocument("cv", JOB.id, {
      generation: { status: "waiting", wait_reason: "consent" },
    }),
  },
};

/** Writing failed, in words. */
export const Failed: Story = {
  name: "Writing failed",
  args: {
    document: postingDocument("cv", JOB.id, {
      generation: {
        status: "failed",
        wait_reason: null,
        error: "run_failed",
      },
    }),
  },
};

/** A letter longer than one page: open it to shorten it. */
export const LetterOverflow: Story = {
  name: "A letter longer than one page",
  args: {
    kind: "letter",
    document: postingDocument("letter", JOB.id, {
      latest: { ...latest, fit: "overflow", pages: 2 },
      generation: { status: "done", wait_reason: null },
    }),
  },
};
