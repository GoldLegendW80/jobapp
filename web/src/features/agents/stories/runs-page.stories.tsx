import type { Meta, StoryObj } from "@storybook/react-vite";

import { AppAt } from "@/design-system/app-at";
import {
  failRunsList,
  fakeRunsApi,
  TEST_AGENT,
} from "@/design-system/fixtures/runs";
import { fakeSession, member } from "@/test/auth";

const meta = {
  title: "Pages/Activity/Assistant activity",
  parameters: {
    layout: "fullscreen",
    page: true,
    docs: {
      description: {
        component:
          "Everything the assistant did for the person, newest first: each run with its status, the posting it worked on, its tokens, its amount and how long it took. Route /runs, from Activity in the account menu.",
      },
    },
  },
  render: () => <AppAt path="/runs" />,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** As a job seeker, I want to see every run of the assistant with its status, cost and duration, so that I know what it did for me and what it used. */
export const SeeWhatTheAssistantDid: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeRunsApi();
  },
};

/** As a new user, I want the empty activity to say what will appear here, so that an empty page does not look broken. */
export const NoActivityYet: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeRunsApi({ runs: [] });
  },
};

/** As a job seeker, I want to see that the activity is loading, so that I wait instead of thinking it is empty. */
export const ActivityLoading: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeRunsApi();
    failRunsList("loading");
  },
};

/** As a job seeker, I want to know when the activity could not be loaded and try again, so that a server error does not pass for no activity. */
export const ActivityUnavailable: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeRunsApi();
    failRunsList("error");
  },
};

/** As the administrator testing a build outside production, I want to start a test run with a recorded stream, so that I can check the run panel before a real agent exists. */
export const StartATestRun: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeRunsApi({ agents: [TEST_AGENT] });
  },
};

/** As a visitor who is not signed in, I want to be sent to sign in first, so that nobody else sees my activity. */
export const SignedOut: Story = {
  beforeEach: () => {
    fakeSession(null);
  },
};

/** As a job seeker on my phone, I want each run to read as one card, so that I can check the activity on the go. */
export const OnAPhone: Story = {
  globals: { viewport: { value: "mobile2", isRotated: false } },
  beforeEach: () => {
    fakeSession(member);
    fakeRunsApi();
  },
};
