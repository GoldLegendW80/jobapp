import type { Meta, StoryObj } from "@storybook/react-vite";

import { AppAt } from "@/design-system/app-at";
import { FILLED, fakeFormShots } from "@/design-system/fixtures/applications";
import {
  fakePostingDocuments,
  postingDocument,
} from "@/design-system/fixtures/documents";
import {
  delayPostingDetail,
  failPostingDetail,
  fakePostingDetails,
  LONG_TITLE_POSTING,
  NO_PAY_NO_FIT_POSTING,
  postingDetail,
} from "@/design-system/fixtures/posting-details";
import { POSTING_DETAIL, POSTINGS } from "@/design-system/fixtures/postings";
import type { JobDetail } from "@/features/jobs/api/jobs-api";
import { fakeApplication, fakeApply } from "@/test/apply";
import { member, fakeSession } from "@/test/auth";
import { fakeVersion } from "@/test/documents";

/** The first invented posting, Senior data engineer at Lumen Freight. */
const first = POSTING_DETAIL;

/** The same posting a few days on: notes, two files, a longer history and an imported field. */
const WORKED_ON: JobDetail = {
  ...postingDetail(first),
  status: "staged",
  notes:
    "Called Camille on Tuesday: the team works in English, two days a week at the office. Send the letter before Friday.",
  files: [
    {
      id: "00000000-0000-4000-a000-000000000001",
      name: "cv.pdf",
      mime: "application/pdf",
      size: 48_213,
      sha256: "ab".repeat(32),
      origin: "generated",
      created_at: "2026-11-02T09:14:00Z",
    },
    {
      id: "00000000-0000-4000-a000-000000000002",
      name: "answers.json",
      mime: "application/json",
      size: 2_048,
      sha256: "cd".repeat(32),
      origin: "upload",
      created_at: "2026-11-03T16:40:00Z",
    },
  ],
  file_count: 2,
  events: [
    {
      id: 3,
      at: "2026-11-03T16:41:00Z",
      kind: "status",
      data: { from: "cv_ready", to: "staged" },
    },
    {
      id: 2,
      at: "2026-11-02T09:15:00Z",
      kind: "fit",
      data: { from: 84, to: 91 },
    },
    {
      id: 1,
      at: `${first.found_on}T07:12:00Z`,
      kind: "created",
      data: { slug: first.slug, via: "sweep" },
    },
  ],
  events_total: 3,
  legacy: { sheet_row: "jobs.csv, row 12" },
};

const meta = {
  title: "Pages/Jobs/Posting",
  parameters: {
    layout: "fullscreen",
    page: true,
    docs: {
      description: {
        component:
          "One posting, route /jobs/:id. The header names it, with the company, the place and the pay on one line, Chat and Edit details. The posting itself (its status, fit, contract and work mode, its text, the competencies and gaps, the link to the original), the notes, the files and the history fill the main column; the side rail holds what the person acts with: Your application with Apply first, the CV and the letter, status and fit, the agent assessment, then the dates and sources. The Jobs list's detail pane shows the same content, stacked when the pane is narrow.",
      },
    },
  },
  beforeEach: () => {
    fakeSession(member);
    fakePostingDetails([
      ...POSTINGS,
      LONG_TITLE_POSTING,
      NO_PAY_NO_FIT_POSTING,
    ]);
  },
  render: () => <AppAt path={`/jobs/${first.id}`} />,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** As a job seeker, I want a posting's pay, fit, skills and text on one page with Apply at hand, so that I can decide and apply without hunting. */
export const ReadAPosting: Story = {};

/** As a job seeker, I want the posting's documents, status, notes, files and history on the same page, so that I find what I did and what comes next. */
export const SeeEverythingAboutAPosting: Story = {
  beforeEach: () => {
    fakePostingDocuments(WORKED_ON, [
      postingDocument("cv", first.id, {
        latest: fakeVersion({ number: 2 }),
        generation: { status: "done", wait_reason: null },
      }),
      postingDocument("letter", first.id, {
        id: "letter-doc",
        latest: fakeVersion(),
        generation: { status: "done", wait_reason: null },
      }),
    ]);
  },
};

/** As a job seeker who ticked Review first, I want the filled form under Your application, so that I approve only what I agree with. */
export const CheckTheFilledForm: Story = {
  beforeEach: () => {
    fakeApply({
      application: fakeApplication("ready", { posting_id: first.id }),
      answers: FILLED,
      reviewBeforeSend: true,
    });
    fakeFormShots();
  },
};

/** As a job seeker, I want a long title and company to wrap in full, so that I know exactly which role this is. */
export const ReadALongTitle: Story = {
  render: () => <AppAt path={`/jobs/${LONG_TITLE_POSTING.id}`} />,
};

/** As a job seeker, I want a posting without pay or score to say so plainly, so that I never mistake a gap for a figure. */
export const SeeAPostingWithoutPayOrFit: Story = {
  render: () => <AppAt path={`/jobs/${NO_PAY_NO_FIT_POSTING.id}`} />,
};

/** As a job seeker, I want to see the posting is on its way, so that a slow answer never looks like an empty page. */
export const WaitForThePosting: Story = {
  beforeEach: () => {
    delayPostingDetail(first.id, 10_000);
  },
};

/** As a job seeker, I want to know when a posting could not be loaded, so that I can try again. */
export const PostingUnavailable: Story = {
  beforeEach: () => {
    failPostingDetail(first.id, 500);
  },
};

/** As a job seeker, I want an old link to a deleted posting to say the page is gone, so that I can go back to my list. */
export const PostingNoLongerExists: Story = {
  beforeEach: () => {
    failPostingDetail(first.id, 404);
  },
};

/** As a job seeker, I want the posting in one column on my phone with Apply near the top, so that I can apply on the go. */
export const ReadAPostingOnMyPhone: Story = {
  globals: { viewport: { value: "mobile2", isRotated: false } },
};
