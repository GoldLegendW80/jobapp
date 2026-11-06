import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";

import { AppAt } from "@/design-system/app-at";
import {
  CAPS,
  fakeApplySettings,
  SAVED_ANSWERS,
} from "@/design-system/fixtures/apply-settings";
import { fakeSession, member } from "@/test/auth";
import { server } from "@/test/server";

const meta = {
  title: "Pages/Settings/Applications",
  parameters: {
    layout: "fullscreen",
    page: true,
    docs: {
      description: {
        component:
          "How the app applies for the person: what Apply does (the review box, unticked by default), what a Like does (Review first or Full auto), the pause switch, the fit thresholds, the company caps and the saved answers. Route /settings/applications.",
      },
    },
  },
  render: () => <AppAt path="/settings/applications" />,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** As a job seeker, I want Apply to fill and send a form without asking me, so that one click is enough. */
export const SendWithoutReviewing: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeApplySettings({ show_postings_from: 60, write_documents_from: 65 });
  },
};

/** As a careful job seeker, I want to tick the review box, so that I check each filled form and send it myself. */
export const ReviewEachFormFirst: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeApplySettings({
      review_before_send: true,
      show_postings_from: 60,
      write_documents_from: 65,
    });
  },
};

/** As a job seeker in Full auto, I want to see that my Likes send what passes every check, so that I know what a Like does now. */
export const LikesInFullAuto: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeApplySettings({
      mode_after_like: "full_auto",
      full_auto_available: true,
      full_auto_since: "2026-11-03T08:00:00Z",
      min_fit: 85,
      min_fit_chosen: true,
      rehearsals_done: 3,
      show_postings_from: 60,
      write_documents_from: 65,
    });
  },
};

/** As a job seeker going on holiday, I want one switch that stops every automatic application, so that nothing is sent while I am away. */
export const PauseAutomaticApplications: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeApplySettings({
      paused: true,
      mode_after_like: "full_auto",
      full_auto_available: true,
      rehearsals_done: 3,
    });
  },
};

/** As a job seeker, I want to limit how often the app applies to one company and reuse my answers, so that I never look careless to a recruiter. */
export const LimitCompaniesAndReuseAnswers: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeApplySettings(
      { show_postings_from: 60, write_documents_from: 65 },
      CAPS,
      SAVED_ANSWERS,
    );
  },
};

/** As a job seeker, I want to know when my settings could not be read, so that I retry instead of changing them blind. */
export const SettingsUnavailable: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeApplySettings();
    server.use(
      http.get("*/api/apply/settings", () =>
        HttpResponse.json({ detail: "Server error" }, { status: 500 }),
      ),
    );
  },
};

/** As a job seeker on my phone, I want to change what Apply does in a few taps, so that I can switch the review on before a big day. */
export const SettingsOnAPhone: Story = {
  globals: { viewport: { value: "mobile2", isRotated: false } },
  beforeEach: () => {
    fakeSession(member);
    fakeApplySettings(
      { show_postings_from: 60, write_documents_from: 65 },
      CAPS,
      SAVED_ANSWERS,
    );
  },
};
