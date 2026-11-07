import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";

import { TemplatePanel } from "@/components/shared/documents/template-panel";
import {
  fakeOneDocument,
  postingDocument,
} from "@/design-system/fixtures/documents";
import { POSTING_DETAIL } from "@/design-system/fixtures/postings";
import { withApp } from "@/design-system/with-app";
import { fakeVersion } from "@/test/documents";
import { server } from "@/test/server";
import { fakeGallery } from "@/test/templates";

const sent = fakeVersion({
  number: 1,
  frozen_at: "2026-11-02T10:00:00Z",
  frozen_reason: "marked_sent",
});
const latest = fakeVersion({ number: 2 });
const CV = postingDocument("cv", POSTING_DETAIL.id, { latest });

const meta = {
  title: "Shared/Documents/Template panel",
  component: TemplatePanel,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "The template of a document: a strip (Carousel) of the templates' thumbnails, the person's own first and the one in use marked; a click switches it, a new version with the same words. Says what the switch changes and opens the gallery with See all. The last section of the document view.",
      },
    },
  },
  args: { document: CV, generation: null },
  beforeEach: () => {
    fakeGallery(fakeOneDocument(CV));
  },
} satisfies Meta<typeof TemplatePanel>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Every template, the one in use marked: click another to switch. */
export const ChooseATemplate: Story = { name: "Choose a template" };

/** A sent version stays as it was: a new template makes a new version. */
export const AfterASentVersion: Story = {
  name: "After a sent version",
  beforeEach: () => {
    fakeGallery(fakeOneDocument(CV, { versions: [latest, sent] }));
  },
};

/** The template in use was removed: Classic prints it until another is chosen. */
export const TemplateRemoved: Story = {
  name: "The template in use was removed",
  args: { document: { ...CV, template_slug: "upload-gone0000" } },
};

/** While the templates are read. */
export const Loading: Story = {
  beforeEach: () => {
    fakeOneDocument(CV);
    server.use(
      http.get("*/api/document-templates", () => new Promise<never>(() => {})),
    );
  },
};

/** The templates could not be read. */
export const CouldNotLoad: Story = {
  name: "Could not be loaded",
  beforeEach: () => {
    fakeOneDocument(CV);
    server.use(
      http.get("*/api/document-templates", () =>
        HttpResponse.json({ detail: "The service is busy." }, { status: 503 }),
      ),
    );
  },
};

/** At a phone's width the strip scrolls sideways, with arrows. */
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
