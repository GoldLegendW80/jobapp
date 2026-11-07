import type { Meta, StoryObj } from "@storybook/react-vite";

import { AppAt } from "@/design-system/app-at";
import { camille } from "@/design-system/fixtures/camille-profile";
import {
  CHAT_MESSAGES,
  fakePostingDocuments,
  postingDocument,
} from "@/design-system/fixtures/documents";
import { POSTING_DETAIL } from "@/design-system/fixtures/postings";
import { fakeSession } from "@/test/auth";
import { fakeVersion } from "@/test/documents";

const JOB = POSTING_DETAIL;
const PATH = `/jobs/${JOB.id}/documents`;

const meta = {
  title: "Pages/Documents/Posting document",
  parameters: {
    layout: "fullscreen",
    page: true,
    docs: {
      description: {
        component:
          "The CV or the letter written for one posting, whole on one page: its page and what the fact check corrected, its text to edit and its template in the main column; the chat, the versions and the details in the side rail (on a phone the header opens the chat). Route /jobs/:id/documents/:kind, opened from the posting page.",
      },
    },
  },
  render: () => <AppAt path={`${PATH}/cv`} />,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

const latest = fakeVersion({ number: 2, created_at: "2026-11-05T09:30:00Z" });
const first = fakeVersion({ number: 1, created_at: "2026-11-04T18:10:00Z" });

/** As a job seeker, I want to read the CV written for this posting as it will be sent, so that I know what the recruiter receives. */
export const ReadTheWrittenCv: Story = {
  beforeEach: () => {
    fakeSession(camille);
    fakePostingDocuments(
      JOB,
      [
        postingDocument("cv", JOB.id, {
          latest,
          generation: { status: "done", wait_reason: null },
        }),
      ],
      [first],
      { consent: { accepted: true } },
    );
  },
};

/** As a job seeker, I want to ask for a change in my own words and see what was changed, so that I fix the CV without editing it field by field. */
export const AskForAChange: Story = {
  beforeEach: () => {
    fakeSession(camille);
    fakePostingDocuments(
      JOB,
      [
        postingDocument("cv", JOB.id, {
          latest,
          generation: { status: "done", wait_reason: null },
        }),
      ],
      [first],
      { consent: { accepted: true }, messages: CHAT_MESSAGES },
    );
  },
};

/** As a job seeker, I want to read what the chat sends before I use it, so that I agree to it knowingly. */
export const ConfirmWhatTheChatSends: Story = {
  beforeEach: () => {
    fakeSession(camille);
    fakePostingDocuments(
      JOB,
      [
        postingDocument("cv", JOB.id, {
          latest,
          generation: { status: "done", wait_reason: null },
        }),
      ],
      [first],
    );
  },
};

/** As a job seeker, I want to see what the fact check removed from my CV, so that I type back what is true. */
export const SeeWhatTheFactCheckCorrected: Story = {
  beforeEach: () => {
    fakeSession(camille);
    const corrected = { ...latest, corrections_count: 1 };
    const api = fakePostingDocuments(
      JOB,
      [
        postingDocument("cv", JOB.id, {
          latest: corrected,
          generation: { status: "done", wait_reason: null },
        }),
      ],
      [first],
      { consent: { accepted: true } },
    );
    api.corrections.set("cv-doc", [
      {
        field_path: "highlights.1",
        before: "Led a team of 40 engineers",
        after: "",
        code: "number",
        by: "code",
        reason: "A number your profile does not state",
      },
    ]);
  },
};

/** As a job seeker, I want to see that my CV is being written, so that I wait for it instead of writing it again. */
export const WaitWhileTheCvIsWritten: Story = {
  beforeEach: () => {
    fakeSession(camille);
    fakePostingDocuments(JOB, [
      postingDocument("cv", JOB.id, {
        generation: { status: "running", wait_reason: null },
      }),
    ]);
  },
};

/** As a job seeker, I want to know why the writer is not starting, so that I give it what it waits for. */
export const WaitingForMyConsent: Story = {
  beforeEach: () => {
    fakeSession(camille);
    fakePostingDocuments(JOB, [
      postingDocument("cv", JOB.id, {
        generation: { status: "waiting", wait_reason: "consent" },
      }),
    ]);
  },
};

/** As a job seeker, I want to be told in words when writing failed, so that I try again. */
export const SeeWhyWritingFailed: Story = {
  beforeEach: () => {
    fakeSession(camille);
    fakePostingDocuments(JOB, [
      postingDocument("cv", JOB.id, {
        generation: {
          status: "failed",
          wait_reason: null,
          error: "run_failed",
        },
      }),
    ]);
  },
};

/** As a job seeker, I want to be warned when the CV runs over one page, so that I shorten it before it is sent. */
export const CvRunsOverOnePage: Story = {
  beforeEach: () => {
    fakeSession(camille);
    fakePostingDocuments(
      JOB,
      [
        postingDocument("cv", JOB.id, {
          latest: { ...latest, fit: "overflow", pages: 2 },
          generation: { status: "done", wait_reason: null },
        }),
      ],
      [first],
    );
  },
};

/** As a job seeker, I want the letter page to say it was never written, so that I go back and write it. */
export const LetterNotWrittenYet: Story = {
  render: () => <AppAt path={`${PATH}/letter`} />,
  beforeEach: () => {
    fakeSession(camille);
    fakePostingDocuments(JOB, [
      postingDocument("cv", JOB.id, {
        latest,
        generation: { status: "done", wait_reason: null },
      }),
    ]);
  },
};

/** As a job seeker on my phone, I want to read the letter before an application leaves, so that I can check it anywhere. */
export const ReadTheLetterOnAPhone: Story = {
  globals: { viewport: { value: "mobile2", isRotated: false } },
  render: () => <AppAt path={`${PATH}/letter`} />,
  beforeEach: () => {
    fakeSession(camille);
    fakePostingDocuments(JOB, [
      postingDocument("letter", JOB.id, {
        latest,
        generation: { status: "done", wait_reason: null },
      }),
    ]);
  },
};
