import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";
import { userEvent, within } from "storybook/test";

import { AppAt } from "@/design-system/app-at";
import { fakeTemplateThumbnails } from "@/design-system/fixtures/documents";
import { POSTINGS } from "@/design-system/fixtures/postings";
import { fakeSession, member } from "@/test/auth";
import {
  fakeCv,
  fakeDocumentOf,
  fakeDocuments,
  fakeVersion,
} from "@/test/documents";
import { server } from "@/test/server";
import { fakeGallery, TEMPLATES } from "@/test/templates";

import type { DocumentOut } from "../api/documents-api";

const meta = {
  title: "Pages/Documents/Templates",
  parameters: {
    layout: "fullscreen",
    page: true,
    docs: {
      description: {
        component:
          "Every look a CV and its letter can take, the person's own first, with filters. Opened from a document, each template offers Use for this document; the words of the document stay the same. Route /documents/templates.",
      },
    },
  },
  render: () => <AppAt path="/documents/templates" />,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** The CV written for the first posting, in the Band look. */
const POSTING_CV: DocumentOut = {
  ...fakeDocumentOf(fakeCv({ id: "b", name: "Main CV" }), {
    latest: fakeVersion(),
  }),
  id: "posting-cv",
  scope: "posting",
  posting_id: POSTINGS[0]?.id ?? null,
  base_document_id: "b",
  name: null,
};

const gallery = (...documents: DocumentOut[]) => {
  fakeSession(member);
  const api = fakeDocuments();
  for (const one of documents) api.documents.set(one.id, one);
  fakeGallery(api);
  fakeTemplateThumbnails(TEMPLATES);
};

/** As a job seeker, I want to see every look my CV and letter can take, so that I pick one that suits the job. */
export const BrowseTemplates: Story = {
  beforeEach: () => {
    gallery();
  },
};

/** As a job seeker, I want to switch the look of one CV without changing its words, so that it fits the company's style. */
export const ChooseALookForADocument: Story = {
  render: () => <AppAt path="/documents/templates?document=posting-cv" />,
  beforeEach: () => {
    gallery(POSTING_CV);
  },
};

/** As a job seeker, I want to see a template's letter too, so that the pair looks right together. */
export const SeeTheLetter: Story = {
  beforeEach: () => {
    gallery();
  },
  play: async ({ canvasElement }) => {
    const page = within(canvasElement);
    const [first] = await page.findAllByRole("button", {
      name: "See the letter",
    });
    if (first) await userEvent.click(first);
  },
};

/** As a job seeker, I want to be told when my filters leave nothing, so that I clear them in one click. */
export const NoTemplateMatches: Story = {
  beforeEach: () => {
    gallery();
  },
  play: async ({ canvasElement }) => {
    const page = within(canvasElement);
    await userEvent.selectOptions(
      await page.findByLabelText("Columns"),
      "Two columns",
    );
    await userEvent.click(
      page.getByLabelText("Easy for hiring software to read"),
    );
  },
};

/** As a job seeker, I want to know when the templates could not be loaded, so that I retry instead of thinking there are none. */
export const TemplatesUnavailable: Story = {
  beforeEach: () => {
    gallery();
    server.use(
      http.get("*/api/document-templates", () =>
        HttpResponse.json({ detail: "Server error" }, { status: 500 }),
      ),
    );
  },
};

/** As a job seeker on my phone, I want two templates side by side, so that I can compare them without zooming. */
export const TemplatesOnAPhone: Story = {
  globals: { viewport: { value: "mobile2", isRotated: false } },
  beforeEach: () => {
    gallery();
  },
};
