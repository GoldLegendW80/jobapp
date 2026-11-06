import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";
import { userEvent, within } from "storybook/test";

import { AppAt } from "@/design-system/app-at";
import { profileCvs } from "@/design-system/fixtures/camille-cvs";
import {
  CAMILLE_ABOUT,
  camille,
  camilleProfile,
  SKILLS_READ,
} from "@/design-system/fixtures/camille-profile";
import { aboutOf, fakeAbout } from "@/test/about";
import { fakeSession } from "@/test/auth";
import { fakeDocuments } from "@/test/documents";
import { fakeProfile, revisionTime } from "@/test/profile";
import { server } from "@/test/server";

const meta = {
  title: "Pages/Profile/Profile",
  parameters: {
    layout: "fullscreen",
    page: true,
    docs: {
      description: {
        component:
          "Everything the search, the documents and the forms read about the person, on one page: Ready for, the CVs, experience and education, About you (Markdown) and contact. Route /profile; `?tab=` opens the classic editor.",
      },
    },
  },
  render: () => <AppAt path="/profile" />,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** Camille's profile, her CVs and her About you document, a model key saved. */
function camilleAccount() {
  fakeSession(camille);
  fakeProfile(camilleProfile());
  fakeDocuments({ cvs: profileCvs() });
  return fakeAbout({ text: CAMILLE_ABOUT, key: "api_key" });
}

/** As a job seeker, I want my whole profile on one page with what it is ready for, so that I know at a glance whether the app can score, write and apply for me. */
export const SeeMyWholeProfile: Story = {
  beforeEach: () => {
    camilleAccount();
  },
};

/** As a new user, I want an empty profile to name the one button that fills each part, so that I know where to start. */
export const StartFromAnEmptyProfile: Story = {
  beforeEach: () => {
    fakeSession(camille);
    fakeProfile(null);
    fakeDocuments();
    fakeAbout({ text: "## Skills\n\n## Answers\n", key: null });
  },
};

/** Camille's account with her Skills part edited and read by the model, `status` so far. */
function skillsBeingRead(status: "running" | "ready") {
  camilleAccount();
  const about = aboutOf(CAMILLE_ABOUT, { key: "api_key", version: 3 });
  server.use(
    http.get("*/api/profile/about", () =>
      HttpResponse.json({
        ...about,
        parts: about.parts.map((part) =>
          part.key === "skills" ? { ...part, state: "edited" } : part,
        ),
        reading: {
          heading: "Skills",
          key: "skills",
          status,
          error: null,
          lines: status === "ready" ? SKILLS_READ : [],
          refused: [],
          can_apply: status === "ready",
          stale: false,
        },
      }),
    ),
  );
}

/** The story opens on the Skills part of About you, far down the page. */
async function showSkills({ canvasElement }: { canvasElement: HTMLElement }) {
  const skills = await within(canvasElement).findByRole("region", {
    name: "Skills",
  });
  skills.scrollIntoView({ block: "start" });
  // Clear of the app's sticky header.
  canvasElement.ownerDocument.defaultView?.scrollBy(0, -96);
}

/** As a job seeker, I want to write my skills as plain text with a live preview, so that I describe them in my own words. */
export const EditMySkills: Story = {
  beforeEach: () => {
    camilleAccount();
  },
  play: async ({ canvasElement }) => {
    await userEvent.click(
      await within(canvasElement).findByRole("button", { name: "Edit Skills" }),
    );
    await showSkills({ canvasElement });
  },
};

/** As a job seeker, I want to see that the app is reading my skills, so that I wait for its answer instead of editing again. */
export const WaitWhileTheAppReadsMySkills: Story = {
  beforeEach: () => {
    skillsBeingRead("running");
  },
  play: showSkills,
};

/** As a job seeker, I want to read what the app understood from my skills before it changes my profile, so that I apply only what is right. */
export const ApplyWhatTheAppUnderstood: Story = {
  beforeEach: () => {
    skillsBeingRead("ready");
  },
  play: showSkills,
};

/** As a job seeker, I want to know when a save of my experience failed, so that I do not leave thinking it is saved. */
export const SeeWhySavingFailed: Story = {
  beforeEach: () => {
    camilleAccount();
    server.use(
      http.put("*/api/profile/experience", () =>
        HttpResponse.json(
          { detail: "The server could not save this section. Try again." },
          { status: 500 },
        ),
      ),
    );
  },
  play: async ({ canvasElement }) => {
    const page = within(canvasElement);
    await userEvent.type(
      await page.findByRole("textbox", { name: "Job title at employer 1" }),
      ", streaming",
    );
    await userEvent.click(
      page.getByRole("button", { name: "Save experience" }),
    );
    const error = await page.findByText(/Experience was not saved/);
    error.scrollIntoView({ block: "center" });
  },
};

/** As a job seeker, I want to know when my profile could not be loaded, so that I can try again instead of thinking it is empty. */
export const ProfileUnavailable: Story = {
  beforeEach: () => {
    fakeSession(camille);
    server.use(
      http.get("*/api/profile", () =>
        HttpResponse.json({ detail: "Server error" }, { status: 500 }),
      ),
    );
  },
};

/** As a job seeker, I want to look back at past saves of a section and restore one, so that a mistake is never final. */
export const LookBackAtPastSaves: Story = {
  beforeEach: () => {
    fakeSession(camille);
    fakeDocuments({ cvs: profileCvs() });
    fakeAbout({ text: CAMILLE_ABOUT, key: "api_key" });
    const now = camilleProfile();
    const profile = fakeProfile(now, 12);
    // Two older saves of Experience: before the move to Lumen Freight, then with its first title.
    const before = structuredClone(now.experience);
    before.employers = before.employers.slice(1);
    const firstTitle = structuredClone(now.experience);
    firstTitle.employers = firstTitle.employers.map((one, index) =>
      index === 0 ? { ...one, title: "Data engineer" } : one,
    );
    profile.revisions.push(
      {
        revision: 3,
        section: "experience",
        created_at: revisionTime(3),
        data: before,
      },
      {
        revision: 5,
        section: "experience",
        created_at: revisionTime(5),
        data: firstTitle,
      },
    );
  },
  play: async ({ canvasElement }) => {
    const body = within(canvasElement.ownerDocument.body);
    await userEvent.click(await body.findByRole("button", { name: "More" }));
    const history = await body.findByRole("menuitem", { name: "History" });
    history.focus();
    await userEvent.keyboard("{ArrowRight}");
    await userEvent.click(
      await body.findByRole("menuitem", { name: "Experience" }),
    );
    await userEvent.click(
      await body.findByRole("button", { name: /Revision 5/ }),
    );
  },
};

/** As a job seeker, I want my profile to read well on my phone, so that I can fix a line between two interviews. */
export const CheckMyProfileOnMyPhone: Story = {
  globals: { viewport: { value: "mobile2", isRotated: false } },
  beforeEach: () => {
    camilleAccount();
  },
};

/** As a job seeker used to the tabs, I want to write my facts in the classic editor, so that I keep the way I worked before. */
export const WriteMyFactsInTheClassicEditor: Story = {
  render: () => <AppAt path="/profile?tab=facts" />,
  beforeEach: () => {
    camilleAccount();
  },
  play: async ({ canvasElement }) => {
    await userEvent.click(
      await within(canvasElement).findByRole("button", {
        name: "Projects, block 2",
      }),
    );
  },
};

/** As a job seeker used to the tabs, I want to sort my skills into what I have, what is close and what I do not do, so that postings are scored the way I would score them. */
export const SortMySkillsInTheClassicEditor: Story = {
  render: () => <AppAt path="/profile?tab=skills" />,
  beforeEach: () => {
    camilleAccount();
  },
};

/** As a job seeker used to the tabs, I want my answers to the usual form questions in one place, so that every application form is filled the same way. */
export const KeepMyAnswersInTheClassicEditor: Story = {
  render: () => <AppAt path="/profile?tab=answers" />,
  beforeEach: () => {
    camilleAccount();
  },
};

/** As a job seeker used to the tabs, I want to edit my identity and languages in the classic editor, so that every document shows them right. */
export const EditMyIdentityInTheClassicEditor: Story = {
  render: () => <AppAt path="/profile?tab=identity" />,
  beforeEach: () => {
    camilleAccount();
  },
};
