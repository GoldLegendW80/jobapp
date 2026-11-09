import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";

import { userEvent, within } from "storybook/test";

import { AppAt } from "@/design-system/app-at";
import { fakeCvPages, profileCvs } from "@/design-system/fixtures/camille-cvs";
import { fakeDocumentReads } from "@/design-system/fixtures/documents";
import {
  camille,
  camilleProfile,
} from "@/design-system/fixtures/camille-profile";
import { fakeSession } from "@/test/auth";
import { fakeCv, fakeDocuments } from "@/test/documents";
import { fakeProfile } from "@/test/profile";
import { fakeGallery } from "@/test/templates";
import { server } from "@/test/server";

const meta = {
  title: "Pages/Profile/CV",
  parameters: {
    layout: "fullscreen",
    page: true,
    docs: {
      description: {
        component:
          "The person's base CVs, the ones every document for a posting is written from: the list is the first tab of the profile page, CVs (/profile, where /profile/cv lands), and each CV has a page of its own below the profile (/profile/cv/:id: its name, Make default, Rename and Delete, then the CV).",
      },
    },
  },
  render: () => <AppAt path="/profile#your-cv" />,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** Camille, her profile and her two CVs; the page images drawn. */
function camilleCvs(cvs = profileCvs()) {
  fakeDocumentReads();
  fakeSession(camille);
  fakeProfile(camilleProfile());
  const documents = fakeDocuments({ cvs });
  fakeGallery(documents);
  fakeCvPages();
  return documents;
}

/** As a job seeker, I want to see all my CVs with the default one marked, so that I know which one the app writes from. */
export const SeeMyCvs: Story = {
  beforeEach: () => {
    camilleCvs();
  },
};

/** As a new user, I want the app to offer to build my first CV from my profile, so that I do not start from a blank page. */
export const BuildMyFirstCv: Story = {
  beforeEach: () => {
    fakeSession(camille);
    fakeProfile(camilleProfile());
    fakeDocuments({ cvs: [] });
  },
};

/** As a new user, I want to see that my first CV is being written, so that I wait for it instead of starting another. */
export const WaitForMyFirstCv: Story = {
  beforeEach: () => {
    camilleCvs([
      fakeCv({
        id: "cv-camille",
        name: "Data engineer CV",
        last_version_at: null,
        generation: {
          id: "g-1",
          status: "running",
          wait_reason: null,
          error: null,
        },
      }),
    ]);
  },
};

/** As a job seeker, I want to read why a CV could not be written, so that I can fix what it needs and try again. */
export const SeeWhyACvFailed: Story = {
  beforeEach: () => {
    camilleCvs([
      ...profileCvs().slice(0, 1),
      fakeCv({
        id: "cv-camille-fr",
        name: "CV en français",
        language: "fr",
        is_default: false,
        last_version_at: null,
        generation: {
          id: "g-2",
          status: "failed",
          wait_reason: null,
          error: "token_limit",
        },
      }),
    ]);
  },
};

/** As a job seeker, I want to open one CV and see its pages, so that I check what an employer will read. */
export const OpenOneCv: Story = {
  render: () => <AppAt path="/profile/cv/cv-camille" />,
  beforeEach: () => {
    camilleCvs();
  },
};

/** As a job seeker, I want a link to a deleted CV to say so, so that I go back to my list instead of waiting. */
export const OpenACvThatNoLongerExists: Story = {
  render: () => <AppAt path="/profile/cv/cv-gone" />,
  beforeEach: () => {
    camilleCvs();
  },
};

/** As a job seeker, I want to rename a CV from its page, so that I tell my CVs apart in the list and on a posting. */
export const RenameMyCv: Story = {
  render: () => <AppAt path="/profile/cv/cv-camille" />,
  beforeEach: () => {
    camilleCvs();
  },
  play: async ({ canvasElement }) => {
    await userEvent.click(
      await within(canvasElement).findByRole("button", {
        name: /^Rename /,
      }),
    );
  },
};

/** As a job seeker, I want deleting a CV to ask me first and say what happens to the default, so that I never lose one by a slip. */
export const DeleteMyCv: Story = {
  render: () => <AppAt path="/profile/cv/cv-camille" />,
  beforeEach: () => {
    camilleCvs();
  },
  play: async ({ canvasElement }) => {
    const body = within(canvasElement.ownerDocument.body);
    await userEvent.click(
      await within(canvasElement).findByRole("button", {
        name: "More actions",
      }),
    );
    await userEvent.click(
      await body.findByRole("menuitem", { name: "Delete this CV" }),
    );
  },
};

/** As a job seeker, I want to know when my CVs could not be loaded, so that I can try again. */
export const CvsUnavailable: Story = {
  beforeEach: () => {
    camilleCvs();
    server.use(
      http.get("*/api/documents/base-cvs", () =>
        HttpResponse.json({ detail: "Server error" }, { status: 500 }),
      ),
    );
  },
};

/** As a job seeker, I want a CV's page to say when the CVs could not be loaded, so that I can try again. */
export const CvUnavailable: Story = {
  render: () => <AppAt path="/profile/cv/cv-camille" />,
  beforeEach: () => {
    camilleCvs();
    server.use(
      http.get("*/api/documents/base-cvs", () =>
        HttpResponse.json({ detail: "Server error" }, { status: 500 }),
      ),
    );
  },
};

/** As a job seeker, I want my CVs to read well on my phone, so that I can check one before an interview. */
export const CheckMyCvsOnMyPhone: Story = {
  globals: { viewport: { value: "mobile2", isRotated: false } },
  beforeEach: () => {
    camilleCvs();
  },
};

/** As a job seeker, I want one CV to read well on my phone, so that I can check it before an interview. */
export const CheckOneCvOnMyPhone: Story = {
  render: () => <AppAt path="/profile/cv/cv-camille" />,
  globals: { viewport: { value: "mobile2", isRotated: false } },
  beforeEach: () => {
    camilleCvs();
  },
};
