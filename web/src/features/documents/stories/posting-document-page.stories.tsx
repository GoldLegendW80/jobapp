import type { Meta, StoryObj } from "@storybook/react-vite";

import { AppAt } from "@/design-system/app-at";
import {
  fakePostingDocuments,
  postingDocument,
} from "@/design-system/fixtures/documents";
import { POSTING_DETAIL } from "@/design-system/fixtures/postings";
import { fakeSession, member } from "@/test/auth";
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
          "The CV or the letter written for one posting: its page, whether it fits one page, its versions, and the views to edit it, chat about it or change its template. Route /jobs/:id/documents/:kind, opened from the posting page.",
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
    fakeSession(member);
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

/** As a job seeker, I want to see that my CV is being written, so that I wait for it instead of writing it again. */
export const WaitWhileTheCvIsWritten: Story = {
  beforeEach: () => {
    fakeSession(member);
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
    fakeSession(member);
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
    fakeSession(member);
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
    fakeSession(member);
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
    fakeSession(member);
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
    fakeSession(member);
    fakePostingDocuments(JOB, [
      postingDocument("letter", JOB.id, {
        latest,
        generation: { status: "done", wait_reason: null },
      }),
    ]);
  },
};
