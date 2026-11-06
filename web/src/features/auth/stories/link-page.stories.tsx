import type { Meta, StoryObj } from "@storybook/react-vite";
import { expect, userEvent, within } from "storybook/test";

import { AppAt } from "@/design-system/app-at";
import {
  fakeLink,
  inviteLink,
  LINK_TOKEN,
} from "@/design-system/fixtures/sign-in";

const meta = {
  title: "Pages/Sign in/Invitation",
  parameters: {
    layout: "fullscreen",
    page: true,
    docs: {
      description: {
        component:
          "The page an invite link opens: the invited address, read only, a name and a password twice. Route /invite/:token; every dead link shows the same page.",
      },
    },
  },
  render: () => <AppAt path={`/invite/${LINK_TOKEN}`} />,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** As an invited friend, I want to choose my name and a password on the page my invite opens, so that I have an account in one step. */
export const SetUpMyAccount: Story = {
  beforeEach: () => {
    fakeLink(inviteLink);
  },
};

/** As an invited friend, I want to be told when the two passwords differ before anything is sent, so that I do not create an account with a typo. This story fills the form itself (play). */
export const PasswordsDoNotMatch: Story = {
  beforeEach: () => {
    fakeLink(inviteLink);
  },
  play: async ({ canvasElement }) => {
    const page = within(canvasElement);
    await userEvent.type(await page.findByLabelText("Name"), "Inès Marlowe");
    await userEvent.type(page.getByLabelText("Password"), "a long passphrase");
    await userEvent.type(
      page.getByLabelText("Confirm password"),
      "a long passfrase",
    );
    await userEvent.click(page.getByRole("button", { name: "Create account" }));
    await expect(
      await page.findByText("The passwords do not match."),
    ).toBeVisible();
  },
};

/** As an invited friend, I want to see that my link is being checked, so that I wait instead of reloading. The app gives up on the request after 5 seconds. */
export const CheckingTheLink: Story = {
  beforeEach: () => {
    fakeLink("pending");
  },
};

/** As an invited friend whose link was used or expired, I want to be told so and how to get a new one, so that I ask the admin instead of trying again. */
export const LinkExpired: Story = {
  beforeEach: () => {
    fakeLink("dead");
  },
};

/** As an invited friend, I want to be told when the link could not be checked and to retry, so that a server hiccup does not look like a dead link. */
export const LinkCheckFailed: Story = {
  beforeEach: () => {
    fakeLink("error");
  },
};

/** As an invited friend on my phone, I want the form in one column with fields big enough to tap, so that I can accept the invite from the e-mail I got. */
export const OnAPhone: Story = {
  globals: { viewport: { value: "mobile2", isRotated: false } },
  beforeEach: () => {
    fakeLink(inviteLink);
  },
};
