import type { Meta, StoryObj } from "@storybook/react-vite";
import { expect, userEvent, within } from "storybook/test";

import { AppAt } from "@/design-system/app-at";
import {
  allAnswered,
  failWizardProfile,
  fakeWizard,
  putOff,
  rolesSaved,
  savedSearch,
} from "@/design-system/fixtures/onboarding";
import { EMPTY } from "@/features/profile/api/profile-api";
import { fakeSession, member } from "@/test/auth";

const meta = {
  title: "Pages/Onboarding/Welcome",
  parameters: {
    layout: "fullscreen",
    page: true,
    docs: {
      description: {
        component:
          "The first login wizard: eight short steps with a progress bar, Back, Next and Skip this step, each saved as it goes. Route /welcome, where a new member lands on first sign in; `?step=` opens a step.",
      },
    },
  },
  render: () => <AppAt path="/welcome" />,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** As a new member, I want the app to open its setup wizard the first time I sign in, so that I do not land on an empty jobs list. */
export const WelcomedOnFirstSignIn: Story = {
  render: () => <AppAt path="/jobs" />,
  beforeEach: () => {
    fakeSession(member);
    fakeWizard();
  },
};

/** As a new member who put the wizard off, I want the jobs page to offer to continue it, so that I can finish setting up when I have time. */
export const ContinueSetupLater: Story = {
  render: () => <AppAt path="/jobs" />,
  beforeEach: () => {
    fakeSession(member);
    fakeWizard({ state: putOff });
  },
};

/** As a new member, I want to start by uploading my CV, so that the app can fill my profile from it while I answer the next steps. */
export const StartWithMyCv: Story = {
  render: () => <AppAt path="/welcome?step=profile" />,
  beforeEach: () => {
    fakeSession(member);
    fakeWizard();
  },
};

/** As a new member, I want to name the roles I am looking for, with optional details folded away, so that the app searches for the right postings. */
export const SayWhatIAmLookingFor: Story = {
  render: () => <AppAt path="/welcome?step=looking" />,
  beforeEach: () => {
    fakeSession(member);
    fakeWizard();
  },
};

/** As a new member, I want Next to tell me when no role is named, so that I never finish a wizard that searches for nothing. This story presses Next itself (play). */
export const NameARoleFirst: Story = {
  render: () => <AppAt path="/welcome?step=looking" />,
  beforeEach: () => {
    fakeSession(member);
    fakeWizard();
  },
  play: async ({ canvasElement }) => {
    const page = within(canvasElement);
    await userEvent.click(await page.findByRole("button", { name: /^Next/ }));
    await expect(await page.findByRole("alert")).toHaveTextContent(
      "Name at least one role, for example Data engineer.",
    );
  },
};

/** As a job seeker, I want to say where I want to work and how remote, so that postings elsewhere stay out of my list. */
export const ChooseWhere: Story = {
  render: () => <AppAt path="/welcome?step=where" />,
  beforeEach: () => {
    fakeSession(member);
    fakeWizard({ state: rolesSaved, search: savedSearch });
  },
};

/** As a job seeker, I want to add the websites the app should search, so that my first search has somewhere to look. */
export const AddWebsitesToSearch: Story = {
  render: () => <AppAt path="/welcome?step=sites" />,
  beforeEach: () => {
    fakeSession(member);
    fakeWizard({ state: rolesSaved, search: savedSearch });
  },
};

/** As a job seeker, I want to tick the contracts I accept, so that the app skips the ones I would never sign. */
export const ChooseContracts: Story = {
  render: () => <AppAt path="/welcome?step=work" />,
  beforeEach: () => {
    fakeSession(member);
    fakeWizard({ state: rolesSaved, search: savedSearch });
  },
};

/** As a job seeker, I want to give my pay range or rather not say, so that the cards can compare a posting's pay with mine. */
export const ShareMyPay: Story = {
  render: () => <AppAt path="/welcome?step=pay" />,
  beforeEach: () => {
    fakeSession(member);
    fakeWizard({ state: rolesSaved, search: savedSearch });
  },
};

/** As a job seeker, I want to choose what a Like does and whether I check each form before it is sent, so that nothing is sent without my approval. */
export const ChooseHowTheAppApplies: Story = {
  render: () => <AppAt path="/welcome?step=apply" />,
  beforeEach: () => {
    fakeSession(member);
    fakeWizard({ state: rolesSaved, search: savedSearch });
  },
};

/** As a job seeker, I want to see every answer as saved, change any of them, and start my first search, so that I begin from a profile I checked. */
export const ReviewAndStart: Story = {
  render: () => <AppAt path="/welcome?step=summary" />,
  beforeEach: () => {
    fakeSession(member);
    fakeWizard({ state: allAnswered, search: savedSearch });
  },
};

/** As a job seeker with no website listed, I want the summary to warn me that the first search will find nothing, so that I add a few websites before I start. */
export const NoWebsitesYet: Story = {
  render: () => <AppAt path="/welcome?step=summary" />,
  beforeEach: () => {
    fakeSession(member);
    fakeWizard({
      state: allAnswered,
      search: { ...savedSearch, interactive_sites: [] },
    });
  },
};

/** As a new member, I want to be told when my profile could not be read and to retry, so that I do not answer steps that would not be saved. */
export const WizardUnavailable: Story = {
  beforeEach: () => {
    fakeSession(member);
    failWizardProfile("error");
  },
};

/** As a new member, I want a quiet loading line while the wizard reads my profile, so that I do not start typing into a page that is not ready. The app gives up on the request after 5 seconds. */
export const WizardLoading: Story = {
  beforeEach: () => {
    fakeSession(member);
    failWizardProfile("pending");
  },
};

/** As a visitor who is not signed in, I want the wizard's address to ask me to sign in first, so that nobody else answers it for me. */
export const SignedOut: Story = {
  beforeEach: () => {
    fakeSession(null);
  },
};

/** As a new member on my phone, I want the progress bar, the step and the buttons to fit the screen, with Back and Next always in reach, so that I can set up the app from my phone. */
export const OnAPhone: Story = {
  globals: { viewport: { value: "mobile2", isRotated: false } },
  render: () => <AppAt path="/welcome?step=where" />,
  beforeEach: () => {
    fakeSession(member);
    fakeWizard({
      state: rolesSaved,
      search: { ...EMPTY.search, title_include: savedSearch.title_include },
    });
  },
};
