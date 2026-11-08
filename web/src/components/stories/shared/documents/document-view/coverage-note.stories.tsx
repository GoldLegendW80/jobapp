import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";

import { CoverageNote } from "@/components/shared/documents/document-view/coverage-note";
import { postingDocument } from "@/design-system/fixtures/documents";
import { POSTING_DETAIL } from "@/design-system/fixtures/postings";
import { withApp } from "@/design-system/with-app";
import { fakeVersion } from "@/test/documents";
import { server } from "@/test/server";

const CV = postingDocument("cv", POSTING_DETAIL.id, {
  latest: fakeVersion({ number: 2 }),
});

const missing = (terms: string[]) => () => {
  server.use(
    http.get("*/api/documents/:id/coverage", () =>
      HttpResponse.json({
        draft_revision: 1,
        terms: [...terms, "Python"],
        missing: terms,
      }),
    ),
  );
};

const meta = {
  title: "Shared/Documents/Coverage note",
  component: CoverageNote,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "Under a posting's CV in the document view: the terms of the posting's requirements, in the person's own words, that the CV does not print. Read again at each revision of the draft; a fact to weigh, never a block, and nothing when every term is printed.",
      },
    },
  },
  args: { document: CV },
  beforeEach: missing(["Kubernetes", "Airflow"]),
} satisfies Meta<typeof CoverageNote>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Two terms missing. */
export const TwoMissing: Story = { name: "Two terms missing" };

/** One term missing. */
export const OneMissing: Story = {
  name: "One term missing",
  beforeEach: missing(["Airflow"]),
};

/** More than eight: the first eight and how many more. */
export const ManyMissing: Story = {
  name: "Many terms missing",
  beforeEach: missing([
    "Kubernetes",
    "Airflow",
    "Terraform",
    "AWS",
    "React",
    "TypeScript",
    "dbt",
    "Spark",
    "Kafka",
    "Go",
  ]),
};

/** Every term printed: nothing is shown. */
export const AllPrinted: Story = {
  name: "Every term printed",
  beforeEach: missing([]),
};
