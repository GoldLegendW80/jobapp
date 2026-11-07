import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";

import { VersionSheet } from "@/components/shared/documents/document-view/version-sheet";
import {
  CV_DRAFT,
  fakeOneDocument,
  postingDocument,
} from "@/design-system/fixtures/documents";
import { POSTING_DETAIL } from "@/design-system/fixtures/postings";
import { withApp } from "@/design-system/with-app";
import { fakeVersion } from "@/test/documents";
import { server } from "@/test/server";

const first = fakeVersion({ number: 1 });
const latest = fakeVersion({ number: 2 });
const CV = postingDocument("cv", POSTING_DETAIL.id, { latest });

const meta = {
  title: "Shared/Documents/Version sheet",
  component: VersionSheet,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "An earlier version opened from the versions of the document view: its page as it was written, read only; Restore makes it the draft to edit.",
      },
    },
  },
  args: { document: CV, version: first, heading: { title: "Version 1" } },
  beforeEach: () => {
    fakeOneDocument(CV, { versions: [latest, first] });
    server.use(
      http.get("*/api/documents/:id/versions/1", () =>
        HttpResponse.json({
          ...first,
          content: CV_DRAFT,
          corrections: [],
          unprintable: [],
        }),
      ),
    );
  },
} satisfies Meta<typeof VersionSheet>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Version 1, read as it was written. */
export const Typical: Story = { name: "An earlier version" };

/** While the version is read. */
export const Loading: Story = {
  beforeEach: () => {
    server.use(
      http.get(
        "*/api/documents/:id/versions/1",
        () => new Promise<never>(() => {}),
      ),
    );
  },
};

/** It could not be read. */
export const CouldNotLoad: Story = {
  name: "Could not be loaded",
  beforeEach: () => {
    server.use(
      http.get("*/api/documents/:id/versions/1", () =>
        HttpResponse.json({ detail: "The service is busy." }, { status: 503 }),
      ),
    );
  },
};
