import type { Meta, StoryObj } from "@storybook/react-vite";
import { expect, userEvent, within } from "storybook/test";

import { AppAt } from "@/design-system/app-at";
import {
  fakeLink,
  LINK_TOKEN,
  resetLink,
} from "@/design-system/fixtures/sign-in";

const meta = {
  title: "Pages/Sign in/Password reset",
  parameters: {
    layout: "fullscreen",
    page: true,
    docs: {
      description: {
        component:
          "The page a password reset link opens: the account's address, read only, and the new password twice. Route /reset/:token; the same page as an invite, without the name.",
      },
    },
  },
  render: () => <AppAt path={`/reset/${LINK_TOKEN}`} />,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** As a job seeker who forgot my password, I want to choose a new one on the page my reset link opens, so that I am signed in again at once. */
export const ChooseANewPassword: Story = {
  beforeEach: () => {
    fakeLink(resetLink);
  },
};

/** As a job seeker, I want a password that breaks the rules refused next to the field with the rule it breaks, so that I fix it before sending. This story fills the form itself (play). */
export const PasswordTooShort: Story = {
  beforeEach: () => {
    fakeLink(resetLink);
  },
  play: async ({ canvasElement }) => {
    const page = within(canvasElement);
    await userEvent.type(await page.findByLabelText("New password"), "short");
    await userEvent.type(page.getByLabelText("Confirm password"), "short");
    await userEvent.click(page.getByRole("button", { name: "Save password" }));
    await expect(
      await page.findByText("Use at least 10 characters for the password."),
    ).toBeVisible();
  },
};

/** As a job seeker whose reset link is more than an hour old, I want to be told it no longer works and how to get a new one, so that I ask the admin. */
export const ResetLinkExpired: Story = {
  beforeEach: () => {
    fakeLink("dead");
  },
};

/** As a job seeker, I want to see that my reset link is being checked, so that I wait instead of reloading. The app gives up on the request after 5 seconds. */
export const CheckingTheLink: Story = {
  beforeEach: () => {
    fakeLink("pending");
  },
};

/** As a job seeker on my phone, I want the two password fields in one column, so that I can reset my password from the e-mail I got. */
export const OnAPhone: Story = {
  globals: { viewport: { value: "mobile2", isRotated: false } },
  beforeEach: () => {
    fakeLink(resetLink);
  },
};
