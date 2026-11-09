import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";

import { Page } from "@/components/blocks/page";
import { DocumentView } from "@/components/shared/documents/document-view";
import {
  CHAT_MESSAGES,
  fakeOneDocument,
  postingDocument,
} from "@/design-system/fixtures/documents";
import { POSTING_DETAIL } from "@/design-system/fixtures/postings";
import { withApp } from "@/design-system/with-app";
import { fakeVersion } from "@/test/documents";
import { server } from "@/test/server";

const latest = fakeVersion({ number: 2, created_at: "2026-11-05T09:30:00Z" });
const first = fakeVersion({ number: 1, created_at: "2026-11-04T18:10:00Z" });
const CV = postingDocument("cv", POSTING_DETAIL.id, {
  latest,
  generation: { status: "done", wait_reason: null },
});
const HEADER = {
  trail: [
    { to: "/jobs", label: "Jobs" },
    { to: `/jobs/${POSTING_DETAIL.id}`, label: POSTING_DETAIL.title },
  ],
  title: `CV for ${POSTING_DETAIL.title} at ${POSTING_DETAIL.company}`,
};

const meta = {
  title: "Shared/Documents/Document view",
  component: DocumentView,
  tags: ["autodocs"],
  decorators: [
    withApp,
    (Story) => (
      <Page>
        <Story />
      </Page>
    ),
  ],
  parameters: {
    layout: "padded",
    docs: {
      description: {
        component:
          "One CV or letter whole on one page: its page edited where it is read, what the fact check corrected and its template in the main column; the chat, the versions, the printed page and the details in the side rail. Shown on /jobs/:id/documents/:kind and for a base CV of the profile.",
      },
    },
  },
  args: { documentId: CV.id, generation: CV.generation, header: HEADER },
  beforeEach: () => {
    fakeOneDocument(CV, {
      versions: [latest, first],
    });
  },
} satisfies Meta<typeof DocumentView>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A posting's CV, its PDF in the header: click a text of the page to change it. */
export const PostingCv: Story = { name: "A posting's CV" };

/** The chat in the rail holds a change asked and answered. */
export const AfterAChange: Story = {
  name: "After a change asked in the chat",
  beforeEach: () => {
    fakeOneDocument(CV, {
      versions: [latest, first],
      messages: CHAT_MESSAGES,
    });
  },
};

/** Without a page header (as the profile embeds it), the PDF links sit with the printed page. */
export const WithoutHeader: Story = {
  name: "Without a page header",
  args: { header: undefined },
};

/** The first version is being written: the page waits, empty. */
export const BeingWritten: Story = {
  name: "First version being written",
  args: { generation: { status: "running", wait_reason: null } },
  beforeEach: () => {
    fakeOneDocument({ ...CV, latest: null, draft: {} }, { versions: [] });
  },
};

/** The page runs over one page: kept as it is, and Make it fit offered. */
export const LongerThanOnePage: Story = {
  name: "Longer than one page",
  beforeEach: () => {
    const over = { ...latest, fit: "overflow" as const, pages: 2 };
    fakeOneDocument({ ...CV, latest: over }, { versions: [over, first] });
  },
};

/** While the document is read. */
export const Loading: Story = {
  beforeEach: () => {
    server.use(
      http.get(`*/api/documents/${CV.id}`, () => new Promise<never>(() => {})),
    );
  },
};

/** The document could not be read: the reason and Retry. */
export const CouldNotLoad: Story = {
  name: "Could not be loaded",
  beforeEach: () => {
    server.use(
      http.get(`*/api/documents/${CV.id}`, () =>
        HttpResponse.json({ detail: "Not found." }, { status: 404 }),
      ),
    );
  },
};

/** At a phone's width everything stacks in reading order (on a real phone the header also opens the chat). */
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
