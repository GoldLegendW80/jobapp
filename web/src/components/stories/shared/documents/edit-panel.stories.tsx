import type { Meta, StoryObj } from "@storybook/react-vite";

import { EditPanel } from "@/components/shared/documents/edit-panel";
import {
  fakeOneDocument,
  postingDocument,
} from "@/design-system/fixtures/documents";
import { POSTING_DETAIL } from "@/design-system/fixtures/postings";
import { withApp } from "@/design-system/with-app";
import { fakeCv, fakeDocumentOf, fakeVersion } from "@/test/documents";

const latest = fakeVersion({ number: 2 });
const CV = postingDocument("cv", POSTING_DETAIL.id, { latest });
const LETTER = postingDocument("letter", POSTING_DETAIL.id, { latest });
const BASE = fakeDocumentOf(
  fakeCv({ id: "cv-camille", name: "Data engineer CV" }),
  {
    latest,
    draft: {
      ...CV.draft,
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
        schools: [],
        languages: [],
      },
    },
  },
);

const meta = {
  title: "Shared/Documents/Edit panel",
  component: EditPanel,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "The draft of a document as its page, edited where it is read and saved by itself, with the language of the page, the save line, and under it what is not printed (what it is for, notes, the profile's facts, a base CV's own facts). The main column of the document view.",
      },
    },
  },
  args: {
    document: CV,
    generation: null,
    heading: { title: "Version 2, the latest" },
  },
  beforeEach: () => {
    fakeOneDocument(CV);
  },
} satisfies Meta<typeof EditPanel>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A posting's CV: click a text, type, and it saves a second after you stop. */
export const PostingCv: Story = { name: "A posting's CV" };

/** A letter: recipient, subject, paragraphs and closing, each where it prints. */
export const Letter: Story = {
  name: "A letter",
  args: { document: LETTER },
  beforeEach: () => {
    fakeOneDocument(LETTER);
  },
};

/** A base CV also edits its own facts, used where the profile is empty. */
export const BaseCv: Story = {
  name: "A base CV with its own facts",
  args: { document: BASE },
  beforeEach: () => {
    fakeOneDocument(BASE);
  },
};

/** An approved version: a change makes a new version to approve. */
export const AfterAnApproval: Story = {
  name: "After an approval",
  beforeEach: () => {
    const approved = { ...latest, approved_at: "2026-11-05T10:00:00Z" };
    fakeOneDocument({ ...CV, latest: approved }, { versions: [approved] });
  },
  args: {
    document: {
      ...CV,
      latest: { ...latest, approved_at: "2026-11-05T10:00:00Z" },
    },
  },
};

/** While the writer works on it, the page says it may replace the draft. */
export const WriterAtWork: Story = {
  name: "While the writer works",
  args: { generation: { status: "running", wait_reason: null } },
};

/** At a phone's width the page and its fields stack. */
export const OnAPhone: Story = {
  name: "On a phone",
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
};
