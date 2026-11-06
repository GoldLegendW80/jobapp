import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";
import { userEvent, within } from "storybook/test";

import { AppAt } from "@/design-system/app-at";
import {
  camille,
  camilleProfile,
  TITLE_IDEAS,
} from "@/design-system/fixtures/camille-profile";
import type { TitleSuggestions } from "@/features/profile/api/title-suggestions-api";
import { fakeSession } from "@/test/auth";
import { fakeProfile } from "@/test/profile";
import { server } from "@/test/server";

const meta = {
  title: "Pages/Settings/Search",
  parameters: {
    layout: "fullscreen",
    page: true,
    docs: {
      description: {
        component:
          "What the search keeps and leaves out, and when documents are written for a posting: job titles with the assistant's ideas, places, contracts, remote stance, minimum fit, never apply rules and the documents cards. Route /settings/search, beside Applications.",
      },
    },
  },
  render: () => <AppAt path="/settings/search" />,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** Camille's search; `ideas` is what the job titles assistant answers, by default no key yet. */
function camilleSearch(ideas?: TitleSuggestions) {
  fakeSession(camille);
  const profile = fakeProfile(camilleProfile());
  if (ideas)
    server.use(
      http.get("*/api/profile/title-suggestions", () =>
        HttpResponse.json(ideas),
      ),
    );
  return profile;
}

/** The story opens on the job title ideas, under Job titles. */
async function showIdeas({ canvasElement }: { canvasElement: HTMLElement }) {
  const ideas = await within(canvasElement).findByText("Suggested for you");
  ideas.scrollIntoView({ block: "center" });
}

/** As a job seeker, I want to say which titles, places and contracts the search keeps, so that I only see postings worth my time. */
export const TellTheSearchWhatToLookFor: Story = {
  beforeEach: () => {
    camilleSearch();
  },
};

/** As a new user, I want the empty search to show what to fill first, so that my first search finds something. */
export const SetUpMyFirstSearch: Story = {
  beforeEach: () => {
    fakeSession(camille);
    fakeProfile(null);
  },
};

/** As a job seeker, I want job title ideas drawn from my profile, so that I search for roles I would not have named myself. */
export const GetJobTitleIdeas: Story = {
  beforeEach: () => {
    camilleSearch(TITLE_IDEAS);
  },
  play: showIdeas,
};

/** As a job seeker, I want to see that the assistant is looking for more ideas, so that I wait for them instead of asking twice. */
export const WaitForMoreIdeas: Story = {
  beforeEach: () => {
    camilleSearch({ ...TITLE_IDEAS, status: "running" });
  },
  play: showIdeas,
};

/** As a job seeker, I want to know when my search settings were not saved, so that I do not wait for postings the search never looks for. */
export const SeeWhySavingFailed: Story = {
  beforeEach: () => {
    camilleSearch(TITLE_IDEAS);
    server.use(
      http.put("*/api/profile/search", () =>
        HttpResponse.json(
          { detail: "The server could not save this section. Try again." },
          { status: 500 },
        ),
      ),
    );
  },
  play: async ({ canvasElement }) => {
    const page = within(canvasElement);
    await userEvent.click(
      await page.findByRole("button", { name: "Save search" }),
    );
  },
};

/** As a job seeker, I want to know when my settings could not be loaded, so that I can try again instead of thinking they are gone. */
export const SettingsUnavailable: Story = {
  beforeEach: () => {
    fakeSession(camille);
    server.use(
      http.get("*/api/profile", () =>
        HttpResponse.json({ detail: "Server error" }, { status: 500 }),
      ),
    );
  },
};

/** As a job seeker, I want to look back at my past search settings and restore one, so that I can undo a change that emptied my list. */
export const LookBackAtPastSearchSettings: Story = {
  beforeEach: () => {
    camilleSearch();
  },
  play: async ({ canvasElement }) => {
    const page = within(canvasElement);
    await userEvent.click(
      await page.findByRole("button", { name: /history/i }),
    );
  },
};

/** As a job seeker, I want to change my search on my phone, so that I can widen it the moment I think of it. */
export const ChangeMySearchOnMyPhone: Story = {
  globals: { viewport: { value: "mobile2", isRotated: false } },
  beforeEach: () => {
    camilleSearch();
  },
};
