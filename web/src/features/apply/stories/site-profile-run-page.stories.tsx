import type { Meta, StoryObj } from "@storybook/react-vite";
import { HttpResponse } from "msw";

import { AppAt } from "@/design-system/app-at";
import {
  EXPERIENCE,
  fakeProfileRun,
  profileRun,
  RUN_ID,
} from "@/design-system/fixtures/site-profile";
import { fakeSession, member } from "@/test/auth";

const meta = {
  title: "Pages/Applications/Site profile run",
  parameters: {
    layout: "fullscreen",
    page: true,
    docs: {
      description: {
        component:
          "The assistant completes the person's candidate profile on a career site, section by section. Nothing is saved on the site before the person approves the section. Route /site-profiles/:id, opened from the Account page.",
      },
    },
  },
  render: () => <AppAt path={`/site-profiles/${RUN_ID}`} />,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** As a job seeker, I want to compare each field on the site with what the assistant would write, so that I approve only what I agree with. */
export const ReviewASectionBeforeSaving: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeProfileRun(profileRun("ready", { sections: [EXPERIENCE] }));
  },
};

/** As a job seeker, I want to see that the assistant is still filling, so that I wait instead of starting again. */
export const FollowTheFilling: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeProfileRun(profileRun("filling"));
  },
};

/** As a job seeker, I want to be told before a site that saves as I type is touched, so that I agree to only empty fields being filled. */
export const AgreeToASiteThatSavesAsYouType: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeProfileRun(
      profileRun("needs_person", { needs: "autosave_confirmation" }),
    );
  },
};

/** As a job seeker, I want to sign in to the site myself, so that the assistant never sees my password. */
export const SignInToTheSite: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeProfileRun(profileRun("needs_person", { needs: "signin" }));
  },
};

/** As a job seeker, I want to know in words why the work stopped, so that I know whether to start again. */
export const SeeWhyItStopped: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeProfileRun(profileRun("failed", { error_code: "fill_interrupted" }));
  },
};

/** As a job seeker, I want to see that my profile on the site is complete, so that I can move on. */
export const ProfileComplete: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeProfileRun(
      profileRun("done", {
        sections: [{ ...EXPERIENCE, state: "saved" }],
      }),
    );
  },
};

/** As a job seeker following an old link, I want to be told the request is gone, so that I go back to my Account page. */
export const RequestNoLongerExists: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeProfileRun(profileRun("ready"), () =>
      HttpResponse.json({ detail: "Not found." }, { status: 404 }),
    );
  },
};

/** As a job seeker on my phone, I want to review a section field by field, so that I can approve it away from my desk. */
export const ReviewOnAPhone: Story = {
  globals: { viewport: { value: "mobile2", isRotated: false } },
  beforeEach: () => {
    fakeSession(member);
    fakeProfileRun(profileRun("ready", { sections: [EXPERIENCE] }));
  },
};
