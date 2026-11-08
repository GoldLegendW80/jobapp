import type { Meta, StoryObj } from "@storybook/react-vite";

import { OfferNotesSection } from "@/components/shared/documents/offer-notes/offer-notes-section";
import { POSTING_DETAIL } from "@/design-system/fixtures/postings";
import { withApp } from "@/design-system/with-app";
import {
  fakeOfferNotes,
  NO_OFFER_NOTES,
  OFFER_NOTES,
} from "@/test/offer-notes";

const JOB = { ...POSTING_DETAIL, status: "interview" };

const meta = {
  title: "Shared/Documents/Offer notes",
  component: OfferNotesSection,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    at: `/jobs/${JOB.id}`,
    docs: {
      description: {
        component:
          "The Offer section of a posting's page, from the interview on: Read an offer takes the letter the person received, and the notes show each clause in plain words under its own words, in the letter's language, what differs from the posting and from the pay sought, and questions for a lawyer. No verdict, no score.",
      },
    },
  },
  args: { job: JOB },
} satisfies Meta<typeof OfferNotesSection>;

export default meta;
type Story = StoryObj<typeof meta>;

/** No letter read yet: what the reading does, and Read an offer. */
export const NothingRead: Story = {
  name: "No letter read yet",
  beforeEach: () => {
    fakeOfferNotes(NO_OFFER_NOTES);
  },
};

/** The letter being read. */
export const Reading: Story = {
  name: "The letter being read",
  beforeEach: () => {
    fakeOfferNotes({
      ...NO_OFFER_NOTES,
      document_id: "offer-notes-1",
      busy: true,
      reading: { id: "reading-1", status: "running", error: null },
      letter: OFFER_NOTES.letter,
    });
  },
};

/** The notes on a French letter, written in French. */
export const NotesInFrench: Story = {
  name: "Notes on a French letter",
  beforeEach: () => {
    fakeOfferNotes(OFFER_NOTES);
  },
};

/** The reading stopped at its limit: read the same letter again. */
export const ReadingStopped: Story = {
  name: "The reading stopped",
  beforeEach: () => {
    fakeOfferNotes({
      ...OFFER_NOTES,
      notes: null,
      version: null,
      reading: { id: "reading-1", status: "failed", error: "token_limit" },
    });
  },
};

/** At a phone's width the clauses stack under their titles. */
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
    fakeOfferNotes(OFFER_NOTES);
  },
};
