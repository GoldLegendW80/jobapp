import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";

import { VersionCorrections } from "@/components/shared/documents/document-view/version-corrections";
import {
  fakeOneDocument,
  postingDocument,
} from "@/design-system/fixtures/documents";
import { POSTING_DETAIL } from "@/design-system/fixtures/postings";
import { withApp } from "@/design-system/with-app";
import { fakeVersion } from "@/test/documents";
import { server } from "@/test/server";

const version = fakeVersion({ number: 2, corrections_count: 2 });
const CV = postingDocument("cv", POSTING_DETAIL.id, { latest: version });
const CORRECTIONS = [
  {
    field_path: "highlights.1",
    before: "Led a team of 40 engineers",
    after: "",
    code: "number",
    by: "code",
    reason: "A number your profile does not state",
  },
  {
    field_path: "summary",
    before: "Ten years of streaming platforms.",
    after: "Seven years of streaming platforms.",
    code: "claim",
    by: "writer",
    reason: "Your profile states seven years",
  },
];

const meta = {
  title: "Shared/Documents/Version corrections",
  component: VersionCorrections,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "What the fact check changed in the version shown, under its page in the document view: each field before and after, and why.",
      },
    },
  },
  args: { documentId: CV.id, version },
  beforeEach: () => {
    fakeOneDocument(CV, { corrections: CORRECTIONS });
  },
} satisfies Meta<typeof VersionCorrections>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Two corrections: one removed, one changed. */
export const Typical: Story = { name: "Two corrections" };

/** While the corrections are read. */
export const Loading: Story = {
  beforeEach: () => {
    server.use(
      http.get(
        "*/api/documents/:id/versions/:number",
        () => new Promise<never>(() => {}),
      ),
    );
  },
};

/** They could not be read. */
export const CouldNotLoad: Story = {
  name: "Could not be loaded",
  beforeEach: () => {
    server.use(
      http.get("*/api/documents/:id/versions/:number", () =>
        HttpResponse.json({ detail: "The service is busy." }, { status: 503 }),
      ),
    );
  },
};
