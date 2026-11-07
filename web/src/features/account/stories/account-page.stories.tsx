import type { Meta, StoryObj } from "@storybook/react-vite";
import { expect, userEvent, within } from "storybook/test";

import { AppAt } from "@/design-system/app-at";
import {
  failAccountReads,
  fakeConnectedAccount,
} from "@/design-system/fixtures/account";
import { fakeSession, member } from "@/test/auth";

const meta = {
  title: "Pages/Account/Account",
  parameters: {
    layout: "fullscreen",
    page: true,
    docs: {
      description: {
        component:
          "The person's own settings: the model key the assistant runs on, their name, their password, Gmail, the websites their browser is signed in to and their other keys. Route /account, from the account menu.",
      },
    },
  },
  render: () => <AppAt path="/account" />,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** As a job seeker, I want to see at a glance which model key, Gmail address, websites and keys my account uses, so that I know what the assistant can do for me. */
export const ManageMyAccount: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeConnectedAccount();
  },
};

/** As a new user, I want each card to say what is not set up yet and how to set it up, so that I know where to start. */
export const NothingConnectedYet: Story = {
  beforeEach: () => {
    fakeSession(member);
  },
};

/** As a job seeker, I want to see that my settings are loading, so that I do not act on a half read page. */
export const SettingsLoading: Story = {
  beforeEach: () => {
    fakeSession(member);
    failAccountReads("loading");
  },
};

/** As a job seeker, I want each card to say when its settings could not be read, so that a server error does not look like nothing is set up. */
export const SettingsUnavailable: Story = {
  beforeEach: () => {
    fakeSession(member);
    failAccountReads("error");
  },
};

/** As a job seeker, I want a new password typed twice differently to be refused before it is sent, so that I never lock myself out with a typo. This story fills the form itself (play). */
export const PasswordsDoNotMatch: Story = {
  beforeEach: () => {
    fakeSession(member);
  },
  play: async ({ canvasElement }) => {
    const page = within(canvasElement);
    const form = within(
      await page.findByRole("form", { name: "Change password" }),
    );
    await userEvent.type(
      form.getByLabelText("Current password"),
      "my current passphrase",
    );
    await userEvent.type(
      form.getByLabelText("New password"),
      "a brand new passphrase",
    );
    await userEvent.type(
      form.getByLabelText("Confirm new password"),
      "a brand new passfrase",
    );
    await userEvent.click(
      form.getByRole("button", { name: "Change password" }),
    );
    await expect(
      await form.findByText("The passwords do not match."),
    ).toBeVisible();
  },
};

/** As a visitor who is not signed in, I want to be sent to sign in first, so that nobody else reaches my settings. */
export const SignedOut: Story = {
  beforeEach: () => {
    fakeSession(null);
  },
};

/** As a job seeker on my phone, I want the cards one under the other at full width, so that I can change a setting on the go. */
export const OnAPhone: Story = {
  globals: { viewport: { value: "mobile2", isRotated: false } },
  beforeEach: () => {
    fakeSession(member);
    fakeConnectedAccount();
  },
};
