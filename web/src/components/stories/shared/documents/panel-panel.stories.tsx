import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";

import { PanelPanel } from "@/components/shared/documents/panel-panel";
import { postingDocument } from "@/design-system/fixtures/documents";
import { POSTING_DETAIL } from "@/design-system/fixtures/postings";
import { withApp } from "@/design-system/with-app";
import type { Panel } from "@/features/documents/api/panel-api";
import { fakeVersion } from "@/test/documents";
import { server } from "@/test/server";

const CV = postingDocument("cv", POSTING_DETAIL.id, {
  latest: fakeVersion({ number: 2 }),
});

const REVIEWS = [
  ["screen", "The automated screen", "PASS"],
  ["manager", "The hiring manager", "INTERVIEW"],
  ["reader_manager", "Reader: the hiring manager", "HIRE"],
  ["reader_rival", "Reader: another applicant", "MAYBE"],
  ["reader_ceo", "Reader: the CEO", "HIRE"],
  ["reader_recruiter", "Reader: the recruiter", "HIRE"],
  ["reader_teammate", "Reader: the teammate", "HIRE"],
] as const;

function panel(overrides: Partial<Panel> = {}): Panel {
  return {
    id: "panel-1",
    status: "done",
    kind: "cv",
    reviewed_version: 1,
    rebuilt_version: 2,
    reviews: REVIEWS.map(([role, label, verdict]) => ({
      role,
      label,
      verdict,
      missing: null,
      line: "Platform Engineer, cloud and backend",
      why: "Says nothing of the payments platform.",
    })),
    mistakes: [
      {
        number: 1,
        quote: "Platform Engineer, cloud and backend",
        why: "Says nothing of the payments platform the posting is about.",
        flagged_by: ["screen", "reader_rival"],
        fix: "Name the payments platform in the headline.",
      },
    ],
    questions: [
      {
        question: "How many services moved to the new platform?",
        section: "experience",
      },
    ],
    screen_before: "REJECT",
    screen_after: "PASS",
    readers_hire: 4,
    readers_answered: 5,
    strong: false,
    closest: null,
    failed: [],
    created_at: "2026-11-06T09:00:00Z",
    finished_at: "2026-11-06T09:05:00Z",
    ...overrides,
  };
}

function serve(answer: Panel | null) {
  server.use(
    http.get("*/api/documents/:id/panel", () =>
      HttpResponse.json({
        panel: answer,
        input_tokens: 1200,
        output_tokens: 300,
      }),
    ),
  );
}

const meta = {
  title: "Shared/Documents/Hiring panel",
  component: PanelPanel,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "The hiring panel of a document: seven reviewers and their verdicts, the mistakes and how the rewrite fixed them, the automated screen before and after, and the facts the panel asks for, each with a link to the profile. A section of the document view; its outcome is one line on the job page's row.",
      },
    },
  },
  args: { document: CV, generation: null },
  beforeEach: () => {
    serve(panel());
  },
} satisfies Meta<typeof PanelPanel>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The panel rewrote the CV: what was wrong, how it was fixed, the screen before and after. */
export const AfterARewrite: Story = { name: "After a rewrite" };

/** Nothing to fix: the reviewer that came closest to rejecting it says why. */
export const Strong: Story = {
  name: "A strong document",
  beforeEach: () => {
    serve(
      panel({
        mistakes: [],
        strong: true,
        rebuilt_version: null,
        screen_before: "PASS",
        screen_after: null,
        closest: {
          role: "screen",
          label: "The automated screen",
          why: "two terms of the posting were missing, the rest held.",
        },
      }),
    );
  },
};

/** The seven reviewers are still reading. */
export const Running: Story = {
  name: "While it reads",
  beforeEach: () => {
    serve(
      panel({
        status: "running",
        rebuilt_version: null,
        mistakes: [],
        screen_after: null,
      }),
    );
  },
};

/** The rewrite did not finish: the first version stays, the mistakes are still to fix. */
export const Failed: Story = {
  name: "The rewrite did not finish",
  beforeEach: () => {
    serve(
      panel({
        status: "failed",
        failed: ["rebuild"],
        rebuilt_version: null,
        screen_after: null,
      }),
    );
  },
};

/** No panel has read the document yet. */
export const NoneYet: Story = {
  name: "Not read yet",
  beforeEach: () => {
    serve(null);
  },
};
