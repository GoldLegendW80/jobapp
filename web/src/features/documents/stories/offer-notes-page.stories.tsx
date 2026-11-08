import type { Meta, StoryObj } from "@storybook/react-vite";

import { AppAt } from "@/design-system/app-at";
import { fakePostingDetails } from "@/design-system/fixtures/posting-details";
import { POSTING_DETAIL } from "@/design-system/fixtures/postings";
import { fakeSession, member } from "@/test/auth";
import { fakeJobDetail } from "@/test/jobs";
import {
  fakeOfferNotes,
  NO_OFFER_NOTES,
  OFFER_NOTES,
} from "@/test/offer-notes";

/** The first invented posting, at the interview stage. */
const AT_INTERVIEW = { ...POSTING_DETAIL, status: "interview" };

const meta = {
  title: "Pages/Jobs/Posting with an offer",
  parameters: {
    layout: "fullscreen",
    page: true,
    docs: {
      description: {
        component:
          "A posting's page, route /jobs/:id, once the status is interview or later: the Offer section under the posting reads the offer letter the person uploads and shows the notes the assistant wrote on it, with their chat.",
      },
    },
  },
  beforeEach: () => {
    fakeSession(member);
    fakePostingDetails();
    fakeJobDetail(AT_INTERVIEW);
  },
  render: () => <AppAt path={`/jobs/${AT_INTERVIEW.id}`} />,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** As a person who received an offer letter, I want to upload it from the posting, so that the assistant reads it with me. */
export const UploadAnOfferLetter: Story = {
  beforeEach: () => {
    fakeOfferNotes(NO_OFFER_NOTES);
  },
};

/** As a person who received an offer letter, I want each clause explained, what differs from the posting and what to ask a lawyer, so that I decide knowing what I sign; nothing tells me to sign. */
export const ReadTheNotesOnMyOffer: Story = {
  beforeEach: () => {
    fakeOfferNotes(OFFER_NOTES);
  },
};

/** As a person whose offer letter is longer than what the assistant reads, I want to be told where the reading stopped, so that I know the end of the letter is not in the notes. */
export const KnowALongLetterWasCut: Story = {
  beforeEach: () => {
    fakeOfferNotes({ ...OFFER_NOTES, truncated: true, read_chars: 30_000 });
  },
};
